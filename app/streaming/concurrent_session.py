# app/streaming/concurrent_session.py
"""
Concurrent session runner for Quranic recitation validation.

This module wraps the existing stable pipeline (STT → SequenceEngine → StateManager)
in a queue-based concurrent architecture, allowing audio capture, transcription, and
validation to happen in parallel without blocking.

Architecture:
  Thread 1 (Capture):    Reads microphone → pushes to audio_queue
  Thread 2 (STT):        Consumes audio_queue → transcribes → pushes to text_queue
  Thread 3 (Validation): Consumes text_queue → validates → prints results

Invariants:
  - Order preserved via job IDs (sequential processing)
  - Single writer to RecitationService (validation thread only)
  - No mutation of SequenceEngine/StateManager from concurrent threads
"""

from queue import Queue, Empty
from threading import Thread, Event
from dataclasses import dataclass
from typing import Optional, Callable
import time
from functools import wraps
import numpy as np
from scipy.io import wavfile
from datetime import datetime
import os

from app.utils.logger import get_logger

logger = get_logger(__name__)

PARTIAL_MATCH_THRESHOLD = 75  # audio score ≥ 75 → reclassify EXTRA+MISSED as partial_match


def timed(method_name: str):
    """
    Decorator to measure and log method execution time.
    
    Args:
        method_name: Human-readable name for the method being timed
    
    Example:
        @timed("transcribe_audio")
        def transcribe(self, audio):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            result = func(*args, **kwargs)
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            print(f"[PERF] {method_name} took {elapsed_ms:.2f} ms")
            return result
        return wrapper
    return decorator


@dataclass
class AudioJob:
    """Audio chunk ready for transcription."""
    id: int
    audio: object
    created_at: float


@dataclass
class TranscriptJob:
    """Transcribed text ready for validation."""
    id: int
    transcript: str
    created_at: float
    stt_latency: float  # seconds


class ConcurrentRecitationSession:
    """
    Non-blocking concurrent session for Quranic recitation validation.
    
    Uses three threads to pipeline:
      1. Audio capture (microphone)
      2. Speech-to-text transcription
      3. Sequence validation and state update
    
    The existing RecitationService and SequenceEngine remain unchanged.
    Only the orchestration layer is new.
    """

    def __init__(
        self,
        service,  # RecitationService instance
        recorder,  # AudioRecorder instance
        printer_fn: Optional[Callable] = None,  # Optional custom printer
    ):
        """
        Initialize concurrent session.
        
        Args:
            service: RecitationService (handles STT, validation, state)
            recorder: AudioRecorder (handles microphone capture)
            printer_fn: Optional function to print results (default: print_results)
        """
        self.service = service
        self.recorder = recorder
        self.printer_fn = printer_fn or self._default_printer

        # Pre-load Whisper model in main thread (prevents OpenMP conflicts on macOS)
        logger.info("Pre-loading Whisper model before concurrent threads...")
        _ = self.service.stt.get_model()
        logger.info("✅ Whisper model pre-loaded")

        # Build reference MFCC dict for Stage 2 pronunciation scoring
        logger.info("Building reference MFCC dict (Stage 2)...")
        from app.tajweed.reference_builder import build_reference_mfccs
        self.reference_mfccs = build_reference_mfccs(
            self.service.stt,
            ayahs=self.service.ayahs,
        )
        logger.info(f"✅ Reference MFCCs ready ({len(self.reference_mfccs)} unique words)")

        # Job queues
        self.audio_queue = Queue(maxsize=5)
        self.text_queue = Queue(maxsize=5)
        self.pronunciation_queue = Queue(maxsize=20)

        # Control
        self.stop_event = Event()

        # Job counter for ordering
        self.job_counter = 0
        self.lock = __import__('threading').Lock()

        # Accumulated mistake results for end-of-session summary
        self._session_results: list[dict] = []

    def start(self):
        """
        Start the concurrent session.
        
        Spawns three daemon threads:
          1. _capture_loop: Reads microphone into audio_queue
          2. _stt_loop: Transcribes from audio_queue → text_queue
          3. _validation_loop: Validates from text_queue, updates state
        
        Blocks until surah is complete or interrupted.
        """
        session_start = time.perf_counter()
        logger.info("Starting concurrent recitation session")
        print("[PERF] session_start")

        # Create threads (daemon=True so they exit when main dies)
        threads = [
            Thread(target=self._capture_loop,      name="CaptureThread",      daemon=True),
            Thread(target=self._stt_loop,          name="STTThread",          daemon=True),
            Thread(target=self._validation_loop,   name="ValidationThread",   daemon=True),
            Thread(target=self._pronunciation_loop, name="PronunciationThread", daemon=True),
        ]

        # Start all threads
        for thread in threads:
            thread.start()
            logger.debug(f"Started {thread.name}")

        try:
            # Main thread: monitor for completion or interruption
            while not self.stop_event.is_set():
                if self.service.state_manager.is_finished():
                    logger.info("Surah complete - stopping session")
                    self.stop_event.set()
                    break
                time.sleep(0.1)

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            self.stop_event.set()

        # Wait for threads to finish
        for thread in threads:
            thread.join(timeout=2)
            if thread.is_alive():
                logger.warning(f"{thread.name} did not exit cleanly")

        elapsed_ms = (time.perf_counter() - session_start) * 1000
        print(f"[PERF] session_complete took {elapsed_ms:.2f} ms")
        logger.info("Concurrent session complete")
        self._print_summary()

    # =====================================================================
    # THREAD 1: AUDIO CAPTURE
    # =====================================================================
    def _capture_loop(self):
        """
        Capture audio from microphone in a loop.

        Records until a natural speech pause (silence-triggered), then pushes
        the complete phrase to audio_queue.  Silence threshold is kept short
        (0.5s) so the capture fires quickly after the user stops speaking,
        while still guaranteeing whole words are never split mid-phoneme.
        """
        import sounddevice as sd

        logger.debug("Capture loop starting")

        SAMPLE_RATE       = 16000
        MIC_CHUNK         = 1024
        SILENCE_THRESHOLD = 0.01
        SILENCE_DURATION  = 0.5    # fire 0.5s after speech ends (was 1.2s)
        MAX_DURATION      = 10.0
        MIN_DURATION      = 0.3    # ignore sub-300ms blips

        chunks_per_sec         = SAMPLE_RATE / MIC_CHUNK
        required_silent_chunks = int(SILENCE_DURATION  * chunks_per_sec)
        max_chunks             = int(MAX_DURATION       * chunks_per_sec)
        min_speech_chunks      = int(MIN_DURATION       * chunks_per_sec)

        print("\n🎤 Speak now — recording fires as soon as you pause...", flush=True)

        try:
            while not self.stop_event.is_set() and not self.service.state_manager.is_finished():
                frames        = []
                silent_chunks = 0
                speech_chunks = 0
                speech_started = False

                with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                    dtype='float32', blocksize=MIC_CHUNK) as stream:
                    for _ in range(max_chunks):
                        if self.stop_event.is_set():
                            break

                        chunk, _ = stream.read(MIC_CHUNK)
                        chunk = chunk.flatten()
                        rms   = float(np.sqrt(np.mean(chunk ** 2)))
                        frames.append(chunk)

                        if rms > SILENCE_THRESHOLD:
                            speech_started = True
                            speech_chunks += 1
                            silent_chunks  = 0
                        elif speech_started:
                            silent_chunks += 1
                            if silent_chunks >= required_silent_chunks:
                                break

                if not speech_started or speech_chunks < min_speech_chunks:
                    continue

                audio = np.concatenate(frames)

                with self.lock:
                    self.job_counter += 1
                    job_id = self.job_counter

                job = AudioJob(id=job_id, audio=audio, created_at=time.time())
                self.audio_queue.put(job)
                logger.debug(f"Capture: Pushed audio job #{job_id}")
                # Save audio as WAV file
                # os.makedirs("debug_audio", exist_ok=True)
                # timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                # wav_path = f"debug_audio/recording_{timestamp}.wav"
                # wavfile.write(wav_path, SAMPLE_RATE, (audio * 32767).astype(np.int16))
                # print(f"💾 Saved to {wav_path}")

        except Exception as e:
            logger.error(f"Capture loop error: {e}")
            self.stop_event.set()
        finally:
            logger.debug("Capture loop exiting")

    # =====================================================================
    # THREAD 2: SPEECH-TO-TEXT
    # =====================================================================
    def _stt_loop(self):
        """
        Transcribe audio from audio_queue.
        
        Reads audio jobs, transcribes them, and pushes results to text_queue
        for validation.
        
        Runs in daemon thread.
        """
        logger.debug("STT loop starting")

        try:
            while not self.stop_event.is_set():
                try:
                    # Non-blocking read from audio_queue
                    job = self.audio_queue.get(timeout=0.5)
                except Empty:
                    continue

                logger.debug(f"STT: Processing audio job #{job.id}")

                try:
                    # Get expected context for Whisper priming
                    expected_context = self.service._get_expected_context()

                    # Transcribe audio (existing WhisperEngine call)
                    stt_start = time.perf_counter()
                    transcript = self.service.stt.transcribe(
                        job.audio,
                        expected_context=expected_context,
                    )
                    stt_latency_ms = (time.perf_counter() - stt_start) * 1000
                    print(f"[PERF] transcribe_audio job#{job.id} took {stt_latency_ms:.2f} ms")

                    logger.debug(f"STT: Transcript (job #{job.id}): '{transcript}' [{stt_latency_ms:.2f}ms]")

                    # Push to text queue for validation
                    text_job = TranscriptJob(
                        id=job.id,
                        transcript=transcript,
                        created_at=job.created_at,
                        stt_latency=stt_latency_ms / 1000,  # Convert back to seconds for storage
                    )
                    self.text_queue.put(text_job)
                    logger.debug(f"STT: Pushed transcript job #{job.id}")

                except Exception as e:
                    logger.error(f"STT error on job #{job.id}: {e}")

                finally:
                    self.audio_queue.task_done()

        except Exception as e:
            logger.error(f"STT loop error: {e}")
            self.stop_event.set()
        finally:
            logger.debug("STT loop exiting")

    # =====================================================================
    # THREAD 3: VALIDATION & STATE UPDATE
    # =====================================================================
    def _validation_loop(self):
        """
        Validate transcripts and update state.
        
        Reads transcripts from text_queue, validates them using existing
        RecitationService, and prints results.
        
        CRITICAL: This is the only thread that mutates RecitationService state!
        
        Runs in daemon thread.
        """
        logger.debug("Validation loop starting")

        try:
            while not self.stop_event.is_set():
                try:
                    # Non-blocking read from text_queue
                    job = self.text_queue.get(timeout=0.5)
                except Empty:
                    continue

                logger.debug(f"Validation: Processing transcript job #{job.id}")

                try:
                    # Skip empty transcripts
                    if not job.transcript.strip():
                        logger.debug(f"Validation: Empty transcript (job #{job.id})")
                        self.text_queue.task_done()
                        continue

                    # Validate transcript (existing RecitationService method)
                    # This call mutates RecitationService state (SINGLE WRITER)
                    validation_start = time.perf_counter()
                    results = self.service.process_transcript(job.transcript)
                    validation_ms = (time.perf_counter() - validation_start) * 1000
                    print(f"[PERF] process_transcript job#{job.id} took {validation_ms:.2f} ms")

                    # Audio reconciliation: reclassify EXTRA+MISSED groups that
                    # acoustically match the expected word(s) as partial_match
                    results = self._reconcile_split_merge(results)

                    # Fill in Expected column for remaining EXTRA entries (display only)
                    self._annotate_extra_context(results)

                    # Accumulate mistakes for end-of-session summary
                    for r in results:
                        if r.get("status") in ("missed", "incorrect", "partial_match"):
                            expected = r.get("expected") or ""
                            ayah_idx = self._ayah_index_for_expected(expected)
                            self._session_results.append({**r, "_ayah_index": ayah_idx})

                    # Print results using printer function
                    self.printer_fn(results)

                    # Stage 2: enqueue correct words for pronunciation scoring
                    self._enqueue_pronunciation_jobs(results)

                    logger.debug(f"Validation: Processed transcript job #{job.id}")

                except Exception as e:
                    logger.error(f"Validation error on job #{job.id}: {e}")

                finally:
                    self.text_queue.task_done()

        except Exception as e:
            logger.error(f"Validation loop error: {e}")
            self.stop_event.set()
        finally:
            logger.debug("Validation loop exiting")

    # =====================================================================
    # STAGE 2: PRONUNCIATION SCORING
    # =====================================================================

    def _enqueue_pronunciation_jobs(self, results: list):
        """
        For every correctly validated word, slice its PCM and push to
        pronunciation_queue.  Uses timestamps from the last Whisper run.
        """
        conf_data = self.service.stt.get_last_word_confidences()
        raw_audio  = self.service.stt.get_last_audio()

        if raw_audio is None or len(raw_audio) == 0:
            return

        for result in results:
            if result.get("status") != "correct":
                continue

            spoken_word = result.get("spoken", "")
            timing = next(
                (w for w in conf_data if w["word"].strip() == spoken_word),
                None,
            )
            if timing is None:
                continue

            start_sample = int(timing["start"] * 16000)
            end_sample   = int(timing["end"]   * 16000)
            word_audio   = raw_audio[start_sample:end_sample]

            if len(word_audio) == 0:
                continue

            expected_uthmani = result.get("expected") or spoken_word
            try:
                self.pronunciation_queue.put_nowait((spoken_word, word_audio, expected_uthmani))
            except Exception:
                pass  # queue full — skip rather than block validation

    def _reconcile_split_merge(self, results: list) -> list:
        """
        Audio reconciliation for STT word-split / word-merge errors.

        Scans results for contiguous groups of EXTRA + MISSED entries.
        For each group: combines audio of EXTRA words, concatenates reference
        MFCCs of MISSED words, runs DTW.  If audio score >= PARTIAL_MATCH_THRESHOLD,
        collapses the group into a single 'partial_match' entry and advances the
        StateManager pointer past the MISSED words (if not already advanced).
        """
        from scipy.spatial.distance import euclidean
        from fastdtw import fastdtw
        from python_speech_features import mfcc as compute_mfcc, delta as compute_delta
        from app.tajweed.reference_builder import (
            lookup_reference_mfcc, _apply_cmvn, _dtw_to_score,
            N_MFCC, N_FILT, N_FFT, WIN_STEP, strip_diacritics,
        )

        conf_data = self.service.stt.get_last_word_confidences()
        raw_audio  = self.service.stt.get_last_audio()
        if raw_audio is None or len(raw_audio) == 0:
            return results

        i = 0
        while i < len(results):
            if results[i]["status"] not in ("extra", "missed"):
                i += 1
                continue

            # Collect contiguous extra/missed run
            group_start = i
            while i < len(results) and results[i]["status"] in ("extra", "missed"):
                i += 1
            group = results[group_start:i]

            extra_entries  = [r for r in group if r["status"] == "extra"]
            missed_entries = [r for r in group if r["status"] == "missed"]
            if not extra_entries or not missed_entries:
                continue

            # Collect audio slices for all EXTRA words
            audio_slices = []
            for r in extra_entries:
                spoken = (r.get("spoken") or "").strip()
                timing = next((w for w in conf_data if w["word"].strip() == spoken), None)
                if timing and timing.get("start") is not None:
                    s = int(timing["start"] * 16000)
                    e = int(timing["end"]   * 16000)
                    if e > s:
                        audio_slices.append(raw_audio[s:e])
            if not audio_slices:
                continue
            combined_audio = np.concatenate(audio_slices)
            if len(combined_audio) < 800:   # < 50ms — too short to score
                continue

            # Collect reference MFCCs for all MISSED words
            ref_parts = []
            for r in missed_entries:
                ref = lookup_reference_mfcc(self.reference_mfccs, r.get("expected") or "")
                if ref is not None:
                    ref_parts.append(ref)
            if not ref_parts:
                continue
            combined_ref = np.vstack(ref_parts)

            # 39D feature extraction — identical pipeline to _pronunciation_loop
            peak = np.max(np.abs(combined_audio))
            if peak > 0:
                combined_audio = combined_audio / peak
            mfcc13    = compute_mfcc(combined_audio, samplerate=16000,
                                     numcep=N_MFCC, nfilt=N_FILT, nfft=N_FFT, winstep=WIN_STEP)
            d1        = compute_delta(mfcc13, 2)
            d2        = compute_delta(d1,     2)
            user_mfcc = _apply_cmvn(np.hstack([mfcc13, d1, d2]))   # (T, 39)

            distance, path = fastdtw(user_mfcc, combined_ref, dist=euclidean)
            i_arr       = np.array([p[0] for p in path])
            j_arr       = np.array([p[1] for p in path])
            frame_costs = np.sqrt(np.sum(
                (user_mfcc[i_arr] - combined_ref[j_arr]) ** 2, axis=1
            ))
            composite = (0.5 * float(np.mean(frame_costs))
                         + 0.5 * float(np.percentile(frame_costs, 90)))
            score = _dtw_to_score(composite)

            spoken_label   = " ".join(r.get("spoken")   or "" for r in extra_entries).strip()
            expected_label = " ".join(r.get("expected") or "" for r in missed_entries).strip()

            logger.debug(
                f"Reconcile [{spoken_label}] vs [{expected_label}]: "
                f"comp={composite:.2f} score={score} "
                f"→ {'partial_match' if score >= PARTIAL_MATCH_THRESHOLD else 'no_match'}"
            )

            if score >= PARTIAL_MATCH_THRESHOLD:
                results[group_start] = {
                    "status":      "partial_match",
                    "spoken":      spoken_label,
                    "expected":    expected_label,
                    "audio_score": score,
                }
                for j in range(group_start + 1, i):
                    results[j]["status"] = "_remove"

                # Advance StateManager past MISSED words if SequenceEngine didn't.
                # SequenceEngine skips advancement when hallucination guard fires
                # (extra_count > correct_count). Guard: only advance if pointer is
                # still AT the missed word, so double-advance is impossible.
                state = self.service.state_manager.get_state()
                for missed_r in missed_entries:
                    missed_word = strip_diacritics(missed_r.get("expected") or "")
                    if state.ayah_index < len(self.service.ayahs):
                        cur = self.service.ayahs[state.ayah_index].words[state.word_index].text
                        if strip_diacritics(cur) == missed_word:
                            self.service.state_manager.move_next_word()
                            state = self.service.state_manager.get_state()

        return [r for r in results if r.get("status") != "_remove"]

    def _annotate_extra_context(self, results: list) -> None:
        """
        For EXTRA entries (expected="-"), fill in the next expected word so the reader
        can see what the system was waiting for. Modifies results in-place. Display-only.
        """
        # Skip "-" sentinel: _format_results hardcodes expected="-" for EXTRA entries.
        # That truthy string must not be treated as a real expected word.
        next_expected = next(
            (r["expected"] for r in results
             if r.get("expected") and r["expected"] != "-"),
            None,
        )

        # Fallback 1: ask the service for the current pointer position
        if next_expected is None:
            ctx = self.service._get_expected_context()
            tokens = ctx.split() if ctx else []
            next_expected = tokens[0] if tokens else None

        # Fallback 2: current word at pointer, clamped to valid bounds.
        # Handles finished recitation (ayah_index past end) or broken state.
        if next_expected is None:
            state = self.service.state_manager.get_state()
            ayahs = self.service.ayahs
            if ayahs:
                idx      = min(state.ayah_index, len(ayahs) - 1)
                word_idx = min(state.word_index, len(ayahs[idx].words) - 1)
                next_expected = ayahs[idx].words[word_idx].text

        if next_expected is None:
            return

        for r in results:
            # Treat "-" sentinel same as None — it means "not yet annotated"
            if r.get("status") == "extra" and r.get("expected") in (None, "", "-"):
                r["expected"] = next_expected

    def _ayah_index_for_expected(self, expected: str) -> int:
        """
        Return the ayah_index for a given expected word text.
        Uses strip_diacritics comparison so diacritics don't cause misses.
        For partial_match entries (space-joined words), uses the first word.
        Falls back to the current state_manager position if not found.
        """
        from app.tajweed.reference_builder import strip_diacritics
        first_word = (expected or "").split()[0] if expected.strip() else ""
        target = strip_diacritics(first_word)
        for idx, ayah in enumerate(self.service.ayahs):
            for word in ayah.words:
                if strip_diacritics(word.text) == target:
                    return idx
        return self.service.state_manager.get_state().ayah_index

    def _pronunciation_loop(self):
        """
        Thread 4: consume pronunciation_queue, compute MFCC + DTW score,
        print result asynchronously after Stage 1 feedback.
        """
        from scipy.spatial.distance import euclidean
        from fastdtw import fastdtw
        from python_speech_features import mfcc as compute_mfcc, delta as compute_delta
        from app.tajweed.reference_builder import (
            lookup_reference_mfcc, _apply_cmvn, _dtw_to_score,
            N_MFCC, N_FEATURES, N_FILT, N_FFT, WIN_STEP,
        )

        logger.debug("Pronunciation loop starting")

        try:
            while not self.stop_event.is_set():
                try:
                    word, user_audio, expected_uthmani = self.pronunciation_queue.get(timeout=0.5)
                except Empty:
                    continue

                try:
                    ref_mfcc = lookup_reference_mfcc(self.reference_mfccs, word)
                    if ref_mfcc is None:
                        logger.debug(f"Pronunciation: no reference for '{word}'")
                        continue

                    # Amplitude normalisation
                    peak = np.max(np.abs(user_audio))
                    if peak > 0:
                        user_audio = user_audio / peak

                    # 39D features: static MFCC + Δ + ΔΔ — same pipeline as reference_builder
                    mfcc13    = compute_mfcc(
                        user_audio,
                        samplerate=16000,
                        numcep=N_MFCC,
                        nfilt=N_FILT,
                        nfft=N_FFT,
                        winstep=WIN_STEP,
                    )                                      # (T, 13)
                    d1        = compute_delta(mfcc13, 2)  # (T, 13) — velocity
                    d2        = compute_delta(d1,     2)  # (T, 13) — acceleration
                    user_mfcc = _apply_cmvn(np.hstack([mfcc13, d1, d2]))  # (T, 39)

                    # ref_mfcc is (T, N_FEATURES=39) — no .T needed
                    distance, path = fastdtw(user_mfcc, ref_mfcc, dist=euclidean)

                    # --- Frame-level costs (vectorized) ---
                    i_arr = np.array([p[0] for p in path])
                    j_arr = np.array([p[1] for p in path])
                    frame_costs = np.sqrt(np.sum(
                        (user_mfcc[i_arr] - ref_mfcc[j_arr]) ** 2, axis=1
                    ))

                    mean_cost = float(np.mean(frame_costs))
                    p90_cost  = float(np.percentile(frame_costs, 90))
                    composite = 0.5 * mean_cost + 0.5 * p90_cost

                    # --- Reference-aligned terminal window ---
                    # Use path entries where the REFERENCE frame index is in the last 10%
                    # of the reference sequence.  This guarantees we examine the actual
                    # terminal phoneme of the reference recording regardless of path length.
                    T_ref      = ref_mfcc.shape[0]
                    term_start = int(T_ref * 0.90)
                    term_mask  = j_arr >= term_start
                    term_costs = frame_costs[term_mask]
                    term_cost  = float(np.mean(term_costs)) if term_mask.any() else mean_cost
                    term_ratio = term_cost / (mean_cost + 1e-8)
                    if term_ratio >= 1.5:
                        penalty   = min(3.0, term_ratio - 1.0)
                        composite = composite * (1.0 + 0.25 * penalty)

                    score = _dtw_to_score(composite)

                    logger.debug(
                        f"DTW [{word}] (exp={expected_uthmani}): "
                        f"mean={mean_cost:.2f} p90={p90_cost:.2f} comp={composite:.2f} "
                        f"term={term_cost:.2f}({term_ratio:.1f}x) score={score}"
                    )
                    print(
                        f"  🎵 {word:<22} {score}/100  "
                        f"(comp={composite:.1f}  p90={p90_cost:.1f}  "
                        f"term={term_cost:.1f}x{term_ratio:.1f})"
                    )

                except Exception as e:
                    logger.error(f"Pronunciation error for '{word}': {e}")

                finally:
                    self.pronunciation_queue.task_done()

        except Exception as e:
            logger.error(f"Pronunciation loop error: {e}")
        finally:
            logger.debug("Pronunciation loop exiting")

    # =====================================================================
    # HELPERS
    # =====================================================================
    def _default_printer(self, results: list):
        """
        Default printer for validation results.
        
        Formats and prints results to terminal in a table.
        """
        print(f"\n  {'Spoken':<22} {'Expected':<22} {'Status':<12}")
        print("  " + "-" * 60)

        for r in results:
            status = r.get("status", "")

            if status == "correct":
                print(f"  {r['spoken']:<22} {r['expected']:<22} ✅")

            elif status == "incorrect":
                print(f"  {r['spoken']:<22} {r['expected']:<22} ❌")

            elif status == "extra":
                expected_word = r.get("expected") or "-"
                print(f"  {r['spoken']:<22} {expected_word:<22} 🚨 EXTRA")

            elif status == "missed":
                print(f"  {'-':<22} {r['expected']:<22} ⚠️  MISSED")

            elif status == "partial_match":
                score = r.get("audio_score", 0)
                print(f"  {r['spoken']:<22} {r['expected']:<22} 🔶 PARTIAL ({score}/100)")

            elif status == "jump":
                print(f"  {r.get('spoken', '-'):<22} {'-':<22} ⏭  JUMP")
                missed = r.get("missed", [])
                if missed:
                    print(f"     Missed: {' '.join(missed)}")

            elif status == "empty":
                print(f"  {'-':<22} {'-':<22} ⚠️  No speech detected")

            elif status == "noise":
                print(f"  {'-':<22} {'-':<22} ⚠️  Noise / unclear speech")

            else:
                print(f"  {r}")

    def _print_summary(self) -> None:
        """
        Print end-of-session recitation summary grouped by ayah.
        Shows missed words, incorrect pronunciations, and partial matches.
        """
        from collections import defaultdict

        mistakes = self._session_results
        surah    = self.service.state_manager.get_state().surah

        W   = 62
        RED = "\033[31m"
        YLW = "\033[33m"
        GRN = "\033[32m"
        RST = "\033[0m"

        print("\n" + "=" * W)
        print("  RECITATION SUMMARY")
        print("=" * W)

        if not mistakes:
            print(f"\n  {GRN}Excellent! No mistakes in this recitation.{RST}\n")
            print("=" * W + "\n")
            return

        by_ayah: dict[int, list] = defaultdict(list)
        for r in mistakes:
            by_ayah[r["_ayah_index"]].append(r)

        for ayah_idx in sorted(by_ayah.keys()):
            ayah_list = by_ayah[ayah_idx]
            ayah_num  = (self.service.ayahs[ayah_idx].ayah_number
                         if ayah_idx < len(self.service.ayahs) else ayah_idx + 1)

            print(f"\n  Ayah {surah}:{ayah_num}")
            print("  " + "-" * (W - 2))

            missed    = [r for r in ayah_list if r["status"] == "missed"]
            incorrect = [r for r in ayah_list if r["status"] == "incorrect"]
            partials  = [r for r in ayah_list if r["status"] == "partial_match"]

            if missed:
                print(f"  {RED}Missed words:{RST}")
                print("    " + "  ".join(r["expected"] for r in missed))

            if incorrect:
                print(f"  {RED}Incorrect words:{RST}")
                for r in incorrect:
                    print(f"    Expected:  {r['expected']}")
                    print(f"    Recited:   {r['spoken']}")

            if partials:
                print(f"  {YLW}Partial matches:{RST}")
                for r in partials:
                    score = r.get("audio_score", 0)
                    print(f"    [{score}/100]  Recited: {r['spoken']}  ->  Expected: {r['expected']}")

        print(f"\n{'=' * W}")
        print(f"  {len(mistakes)} mistake(s) across {len(by_ayah)} ayah(s)")
        print("=" * W + "\n")
