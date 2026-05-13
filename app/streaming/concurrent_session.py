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

from app.utils.logger import get_logger

logger = get_logger(__name__)


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

        # CRITICAL FIX: Pre-load Whisper model BEFORE creating threads
        # This prevents OpenMP initialization conflicts (macOS issue)
        # Load the model in main thread, then threads can reuse it safely
        logger.info("Pre-loading Whisper model before concurrent threads...")
        _ = self.service.stt.get_model()  # Force model load in main thread
        logger.info("✅ Whisper model pre-loaded")

        # Job queues
        self.audio_queue = Queue(maxsize=5)  # Limit queue size
        self.text_queue = Queue(maxsize=5)

        # Control
        self.stop_event = Event()

        # Job counter for ordering
        self.job_counter = 0
        self.lock = __import__('threading').Lock()

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
            Thread(target=self._capture_loop, name="CaptureThread", daemon=True),
            Thread(target=self._stt_loop, name="STTThread", daemon=True),
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

    # =====================================================================
    # THREAD 1: AUDIO CAPTURE
    # =====================================================================
    def _capture_loop(self):
        """
        Capture audio from microphone in a loop.
        
        Each time user finishes speaking (detected via pause), record audio
        and push to audio_queue for transcription.
        
        Runs in daemon thread - exits when stop_event is set.
        """
        logger.debug("Capture loop starting")

        try:
            while not self.stop_event.is_set() and not self.service.state_manager.is_finished():
                # Prompt user for current position
                state = self.service.state_manager.get_current_position()
                expected_word = state.get("word", "?")

                print(f"\n▶  Ayah {state['ayah_index'] + 1} | Expecting: {expected_word}")
                print("🎤 Speak now (stops automatically when you pause)...", flush=True)

                # Record audio (blocking call - OK, it's in its own thread)
                audio = self.recorder.record_until_pause()

                if audio is None:
                    logger.debug("Capture: No audio recorded (silence?)")
                    continue

                # Assign job ID
                with self.lock:
                    self.job_counter += 1
                    job_id = self.job_counter

                # Push to audio queue
                job = AudioJob(
                    id=job_id,
                    audio=audio,
                    created_at=time.time(),
                )
                self.audio_queue.put(job)
                logger.debug(f"Capture: Pushed audio job #{job_id}")

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
