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

from app.utils.logger import get_logger

logger = get_logger(__name__)

# --- Overlapping-chunk transcription (boundary de-hallucination) -------------
# Consecutive captures share OVERLAP_SECONDS of audio so a word split across a
# silence boundary is never clipped: the boundary region is transcribed with
# full context in one of the two chunks, and the duplicate is removed by
# SequenceMatcher stitching before validation. If the user resumes after a gap
# longer than OVERLAP_RESET_GAP_S, the chunks are treated as unrelated (no
# overlap, no stitch). Set ENABLE_OVERLAP_STITCH=False to fully disable.
ENABLE_OVERLAP_STITCH = True
OVERLAP_SECONDS       = 1.5
OVERLAP_RESET_GAP_S   = 3.0   # gap (s) of silence beyond which we don't stitch


def timed(method_name: str):
    """
    Decorator to measure and log method execution time.
    This pipeline gets triggered through a crone job that takes all the repositories of the last 1-2 hrs
    that gets pushed to production, and ingest all those links inside the application db along with a
    PENDING Status. 
    
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
    # True when this chunk was prepended with the tail of the previous chunk
    # (so the STT loop should de-duplicate the shared overlap region). The flag
    # rides on the job — NOT a shared instance var — because capture and STT run
    # on separate threads and capture may be several jobs ahead of STT.
    overlapped: bool = False


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

        # Job queues
        self.audio_queue = Queue(maxsize=5)
        self.text_queue = Queue(maxsize=5)

        # Control
        self.stop_event = Event()

        # Job counter for ordering
        self.job_counter = 0
        self.lock = __import__('threading').Lock()

        # Accumulated mistake results for end-of-session summary
        self._session_results: list[dict] = []

        # Overlapping-chunk transcription state.
        # _overlap_tail is owned by the capture thread; _prev_transcript by the STT thread.
        self._overlap_tail = None      # last OVERLAP_SECONDS of the previous fresh recording
        self._prev_transcript = ""     # full transcript of the previous chunk (for stitching)

        # Optional WebSocket event sink (set via set_ws_mode). When present, the
        # validation loop emits transcript/validation_result events and start()
        # emits a summary event, in addition to the normal terminal printing.
        self._ws_cb: Optional[Callable[[str, dict], None]] = None

    def set_ws_mode(self, event_cb: Callable[[str, dict], None]) -> None:
        """
        Route session events to a WebSocket sink instead of (well, in addition to)
        the terminal. event_cb(event_type, payload) is called from the validation
        thread for 'transcript' and 'validation_result', and from start() for
        'summary'. Terminal printing still happens, so this is non-destructive.
        """
        self._ws_cb = event_cb

    def _emit(self, event_type: str, payload: dict) -> None:
        """Forward an event to the WebSocket sink if one is registered."""
        if self._ws_cb is not None:
            try:
                self._ws_cb(event_type, payload)
            except Exception as e:
                logger.error(f"ws event_cb error on '{event_type}': {e}")

    def _pointer(self) -> dict:
        """Current expected position from the state manager."""
        return {
            "ayah":       self.service.state_manager.get_current_ayah_number(),
            "word_index": self.service.state_manager.get_state().word_index,
        }

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
            Thread(target=self._capture_loop,    name="CaptureThread",    daemon=True),
            Thread(target=self._stt_loop,        name="STTThread",        daemon=True),
            Thread(target=self._validation_loop, name="ValidationThread", daemon=True),
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

        # Push the end-of-session summary to the WebSocket client. ws_recitation
        # transforms _session_results (with _ayah_index/status) into the
        # frontend's {ayah, type, expected, spoken} shape.
        self._emit("summary", {
            "mistakes": list(self._session_results),
            "stats":    self.service.get_stats(),
        })

    # =====================================================================
    # OVERLAP STITCHING
    # =====================================================================
    def _dedupe_overlap(self, prev: str, curr: str, similarity_threshold: float = 0.80) -> str:
        """
        Return ONLY the new words of `curr` after removing the region it shares
        with the tail of `prev` (the prepended-audio overlap, re-transcribed).

        Unlike a batch transcript stitcher (which would return prev+curr merged),
        this forwards just the remainder because `prev` was already validated in
        the previous chunk — re-sending its words would double-count.

        Because the overlap is always prev's TAIL re-appearing as curr's HEAD
        (the prepended audio), we match word-for-word at the boundary: find the
        largest k where prev's last k words ≈ curr's first k words (fuzzy, to
        tolerate boundary mis-transcription) and drop those k words from curr.
        """
        from difflib import SequenceMatcher

        pw = prev.strip().split()
        cw = curr.strip().split()
        if not pw or not cw:
            return curr.strip()

        max_k = min(len(pw), len(cw))
        for k in range(max_k, 0, -1):
            tail = " ".join(pw[-k:])
            head = " ".join(cw[:k])
            if SequenceMatcher(None, tail, head).ratio() >= similarity_threshold:
                return " ".join(cw[k:]).strip()  # may be "" if curr is all overlap

        # No boundary overlap detected — forward current chunk unchanged.
        return curr.strip()

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

        OVERLAP_SAMPLES = int(OVERLAP_SECONDS * SAMPLE_RATE)

        chunks_per_sec         = SAMPLE_RATE / MIC_CHUNK
        required_silent_chunks = int(SILENCE_DURATION  * chunks_per_sec)
        max_chunks             = int(MAX_DURATION       * chunks_per_sec)
        min_speech_chunks      = int(MIN_DURATION       * chunks_per_sec)

        print("\n🎤 Speak now — recording fires as soon as you pause...", flush=True)

        last_push_time = None  # wall-clock when the previous chunk was pushed

        try:
            while not self.stop_event.is_set() and not self.service.state_manager.is_finished():
                frames        = []
                silent_chunks = 0
                speech_chunks = 0
                speech_started = False
                speech_start_time = None

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
                            if not speech_started:
                                speech_start_time = time.time()
                            speech_started = True
                            speech_chunks += 1
                            silent_chunks  = 0
                        elif speech_started:
                            silent_chunks += 1
                            if silent_chunks >= required_silent_chunks:
                                break

                if not speech_started or speech_chunks < min_speech_chunks:
                    continue

                new_audio = np.concatenate(frames)

                # ── Overlapping-chunk stitching ──────────────────────────────
                # Prepend the previous chunk's tail so the boundary word is never
                # clipped. Skip if the user resumed after a long gap (unrelated
                # phrase) — the per-job flag tells STT whether to de-duplicate.
                overlapped = False
                audio = new_audio
                if ENABLE_OVERLAP_STITCH and self._overlap_tail is not None:
                    gap = (speech_start_time - last_push_time) if (speech_start_time and last_push_time) else 0.0
                    if gap <= OVERLAP_RESET_GAP_S:
                        audio = np.concatenate([self._overlap_tail, new_audio])
                        overlapped = True

                # Save the tail of THIS fresh recording for the next iteration
                # (from new_audio, never the prepended copy, so it can't compound).
                if ENABLE_OVERLAP_STITCH:
                    self._overlap_tail = (new_audio[-OVERLAP_SAMPLES:]
                                          if len(new_audio) >= OVERLAP_SAMPLES else new_audio)

                with self.lock:
                    self.job_counter += 1
                    job_id = self.job_counter

                last_push_time = time.time()
                job = AudioJob(id=job_id, audio=audio, created_at=last_push_time, overlapped=overlapped)
                self.audio_queue.put(job)
                logger.debug(f"Capture: Pushed audio job #{job_id} (overlapped={overlapped})")
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

                    # ── Overlap stitching ────────────────────────────────────
                    # This chunk shares its leading audio with the previous one.
                    # Remove the duplicated overlap region and forward ONLY the
                    # new words — the previous chunk's words were already
                    # validated last round (the validator advances a pointer, so
                    # re-sending them would double-count as spurious EXTRA).
                    if not job.overlapped:
                        # First chunk, or resumed after a long gap → fresh segment.
                        self._prev_transcript = ""

                    if job.overlapped and self._prev_transcript:
                        new_text = self._dedupe_overlap(self._prev_transcript, transcript)
                        logger.debug(
                            f"STT stitch (job #{job.id}): prev='{self._prev_transcript}' "
                            f"| curr='{transcript}' | new='{new_text}'"
                        )
                    else:
                        new_text = transcript

                    # Remember the FULL current transcript for the next stitch.
                    self._prev_transcript = transcript

                    # Push only the new words to validation.
                    text_job = TranscriptJob(
                        id=job.id,
                        transcript=new_text,
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
                    # Skip empty transcripts (finally handles task_done)
                    if not job.transcript.strip():
                        logger.debug(f"Validation: Empty transcript (job #{job.id})")
                        continue

                    # Surface the raw transcript before alignment (WS only)
                    self._emit("transcript", {"text": job.transcript})

                    # Snapshot the expected pointer BEFORE validation so the WS layer
                    # can attach (ayah, word_index) to each result entry.
                    pre = self._pointer()

                    # Validate transcript (existing RecitationService method)
                    # This call mutates RecitationService state (SINGLE WRITER)
                    validation_start = time.perf_counter()
                    results = self.service.process_transcript(job.transcript)
                    validation_ms = (time.perf_counter() - validation_start) * 1000
                    print(f"[PERF] process_transcript job#{job.id} took {validation_ms:.2f} ms")

                    # Fill in Expected column for remaining EXTRA entries (display only)
                    self._annotate_extra_context(results)

                    # Accumulate mistakes for end-of-session summary
                    for r in results:
                        if r.get("status") in ("missed", "incorrect", "partial"):
                            expected = r.get("expected") or ""
                            ayah_idx = self._ayah_index_for_expected(expected)
                            self._session_results.append({**r, "_ayah_index": ayah_idx})

                    # Emit per-word validation to the WebSocket (positions attached
                    # server-side in ws_recitation via _pre_ayah/_pre_word).
                    self._emit("validation_result", {
                        "results":         results,
                        "current_pointer": self._pointer(),
                        "_pre_ayah":       pre["ayah"],
                        "_pre_word":       pre["word_index"],
                    })

                    # Print results using printer function
                    self.printer_fn(results)

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

    def _annotate_extra_context(self, results: list) -> None:
        """
        For EXTRA entries (expected="-"), fill in the next expected word so the reader
        can see what the system was waiting for. Modifies results in-place. Display-only.
        """
        # The state-manager pointer (after process_transcript) sits at the first
        # un-consumed expected word — exactly the word a trailing EXTRA failed to
        # match. Use it directly. (Do NOT scan results for a non-dash expected: the
        # first such hit is a matched CORRECT word behind the pointer, which gave
        # the wrong "expected" column for mispronounced-as-EXTRA words.)
        next_expected = None
        state = self.service.state_manager.get_state()
        ayahs = self.service.ayahs
        if ayahs:
            idx      = min(state.ayah_index, len(ayahs) - 1)
            word_idx = min(state.word_index, len(ayahs[idx].words) - 1)
            next_expected = ayahs[idx].words[word_idx].text

        # Fallback: expected-context tokens (also pointer-derived) if state unusable.
        if next_expected is None:
            ctx = self.service._get_expected_context()
            tokens = ctx.split() if ctx else []
            next_expected = tokens[0] if tokens else None

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
        Falls back to the current state_manager position if not found.
        """
        from app.nlp.normalizer import strip_diacritics
        first_word = (expected or "").split()[0] if expected.strip() else ""
        target = strip_diacritics(first_word)
        for idx, ayah in enumerate(self.service.ayahs):
            for word in ayah.words:
                if strip_diacritics(word.text) == target:
                    return idx
        return self.service.state_manager.get_state().ayah_index

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

            elif status == "partial":
                print(f"  {r['spoken']:<22} {r['expected']:<22} 🟡 PARTIAL")

            elif status == "extra":
                expected_word = r.get("expected") or "-"
                print(f"  {r['spoken']:<22} {expected_word:<22} 🚨 EXTRA")

            elif status == "missed":
                print(f"  {'-':<22} {r['expected']:<22} ⚠️  MISSED")

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
            partial   = [r for r in ayah_list if r["status"] == "partial"]

            if missed:
                print(f"  {RED}Missed words:{RST}")
                print("    " + "  ".join(r["expected"] for r in missed))

            if incorrect:
                print(f"  {RED}Incorrect words:{RST}")
                for r in incorrect:
                    print(f"    Expected:  {r['expected']}")
                    print(f"    Recited:   {r['spoken']}")

            if partial:
                print(f"  {YLW}Minor diacritic slips:{RST}")
                for r in partial:
                    print(f"    Expected:  {r['expected']}")
                    print(f"    Recited:   {r['spoken']}")

        n_partial  = sum(1 for r in mistakes if r["status"] == "partial")
        n_mistakes = len(mistakes) - n_partial
        print(f"\n{'=' * W}")
        print(f"  {n_mistakes} mistake(s) + {n_partial} minor slip(s) across {len(by_ayah)} ayah(s)")
        print("=" * W + "\n")
