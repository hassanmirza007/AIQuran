# app/services/recitation_service.py

import time
from app.data.quran_loader import load_surah
from app.stt.whisper_engine import WhisperEngine
from app.audio.recorder import record_audio
from app.utils.logger import get_logger
from app.core.sequence_engine_v2 import SequenceEngineV2
from app.core.state_manager import StateManager
from app.core.models import SessionStats

logger = get_logger(__name__)


class RecitationService:

    def __init__(self, surah_file: str, surah_number: int = 1, model_size: str = "small"):
        # Load Quran data
        self.ayahs = load_surah(surah_file)
        logger.info(f"Loaded surah {surah_number} — {len(self.ayahs)} ayahs")

        # Store surah number for locking purposes
        self.surah_number = surah_number

        # Initialize state manager (tracks cursor position)
        self.state_manager = StateManager(surah=surah_number, ayahs=self.ayahs)

        # Track whether position has been locked (auto-detected or explicit)
        self.is_locked = False

        # Convert to SequenceEngine format (for validation logic)
        self.quran_data = self._prepare_quran_data(self.ayahs)

        # Initialize components
        self.sequence_engine = SequenceEngineV2(
            self.quran_data,
            state_manager=self.state_manager,
            surah_number=surah_number
        )
        self.stt = WhisperEngine(model_size=model_size)
        
        # Statistics tracking
        self._stats = SessionStats()

    # ------------------------------------------------------------------ #
    #  Quran Data Adapter
    # ------------------------------------------------------------------ #

    def _prepare_quran_data(self, ayahs):
        formatted = []

        for ayah in ayahs:
            words = [w.text for w in ayah.words]
            formatted.append({
                "ayah": ayah.ayah_number,
                "words": words
            })

        return formatted

    # ------------------------------------------------------------------ #
    #  Context for Whisper
    # ------------------------------------------------------------------ #

    def _get_expected_context(self) -> str:
        """Get expected context for Whisper priming."""
        state = self.state_manager.get_state()
        ayah_index = state.ayah_index
        word_index = state.word_index

        if ayah_index >= len(self.ayahs):
            return ""

        ayah = self.ayahs[ayah_index]
        start = word_index
        end = min(start + 5, len(ayah.words))

        return " ".join(w.text for w in ayah.words[start:end])

    # ------------------------------------------------------------------ #
    #  Audio pipeline (MAIN ENTRY)
    # ------------------------------------------------------------------ #

    def process_audio_input(self) -> list[dict]:
        start = time.perf_counter()
        
        audio = record_audio()

        expected_context = self._get_expected_context()

        transcript = self.stt.transcribe(audio, expected_context=expected_context)

        logger.info(f"Transcribed : '{transcript}'")
        logger.info(f"Expected ctx: '{expected_context}'")

        result = self.process_transcript(transcript)
        
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"[PERF] process_audio_input took {elapsed_ms:.2f} ms")
        
        return result

    # ------------------------------------------------------------------ #
    #  🔥 TEXT → CLEAN → ENGINE
    # ------------------------------------------------------------------ #

    def process_transcript(self, transcript: str, is_first_chunk: bool = False) -> list[dict]:
        """
        Process a transcript and validate against Quran.
        
        Args:
            transcript: Spoken text to validate
            is_first_chunk: If True, attempt start detection (auto-lock position)
        
        Returns:
            List of validation results, or empty list if no speech detected
        """
        start = time.perf_counter()

        if not transcript.strip():
            # No speech detected - return empty list (don't send to UI)
            # This prevents cursor advancement and stat updates
            return []

        # ✅ START DETECTION: On first chunk, auto-detect starting position
        if is_first_chunk and not self.is_locked:
            from app.services.start_detector import StartDetector
            
            detector = StartDetector(self.ayahs, threshold=0.78, min_words=2)
            detection = detector.detect_start(transcript)
            
            if detection and detection.get("confidence", 0) >= 0.78:
                # Auto-detected with high confidence - lock to this position
                try:
                    self.state_manager.seek(
                        ayah_index=detection["ayah_index"],
                        word_index=detection.get("word_index", 0)
                    )
                    self.is_locked = True
                    logger.info(
                        f"✅ Auto-detected start: Ayah {detection['ayah_number']}, "
                        f"Word {detection.get('word_index', 0)}, "
                        f"Confidence {detection['confidence']:.2f}"
                    )
                except (ValueError, KeyError) as e:
                    logger.error(f"❌ Invalid detection result: {e}")
                    detection_failed = [{
                        "spoken": transcript,
                        "expected": "-",
                        "status": "detection_failed",
                        "message": f"Could not lock to detected position: {e}"
                    }]
                    self.record_result(detection_failed)
                    return detection_failed
            else:
                # Detection failed or low confidence - cannot proceed
                detection_failed = [{
                    "spoken": transcript,
                    "expected": "-",
                    "status": "detection_failed",
                    "message": "Could not detect starting position. Please start with clearer audio from the beginning of an Ayah."
                }]
                self.record_result(detection_failed)
                return detection_failed

        # 🔥 Step 1: RAW words (NO FILTER)
        raw_words = transcript.split()

        # 🔥 Step 2: Get confidence data for ALL words
        conf_data = self.stt.get_last_word_confidences()

        # 🔥 Step 3: CRITICAL FIX - Filter out ULTRA-LOW confidence words (hallucinations)
        # Words with confidence < 0.10 are almost certainly Whisper hallucinations
        # The engine should NOT try to match these at all
        # But words >= 0.10 should be passed to engine (even if low)
        filtered_words = []
        conf_dict = {w["word"]: w["confidence"] for w in conf_data}
        
        for word in raw_words:
            conf = conf_dict.get(word, 0.0)
            if conf >= 0.10:  # Only keep words with SOME actual confidence
                filtered_words.append(word)
            else:
                logger.debug(f"Filtering out hallucination: '{word}' (conf={conf:.2f})")

        # 🔥 Step 4: IMPORTANT: Pass filtered words to engine
        # Words with confidence < 0.10 are filtered out (Whisper hallucinations)
        # For remaining words: engine has adaptive thresholds and will decide which to match
        # Do NOT pre-filter low-confidence words here — let the engine decide
        # Previously filtering before alignment caused valid low-confidence
        # Quran words to be completely dropped from matching
        results = self.sequence_engine.process(
            raw_words=raw_words,
            filtered_words=filtered_words,  # ← Filter out ultra-low confidence only
            word_confidences=conf_data  # ← Engine uses this for adaptive thresholds
        )

        formatted = self._format_results(results)
        self.record_result(results)
        
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"[PERF] process_transcript took {elapsed_ms:.2f} ms")
        
        return formatted

    # ------------------------------------------------------------------ #
    #  🔧 Helpers
    # ------------------------------------------------------------------ #

    def _dedupe(self, words):
        result = []
        for w in words:
            if result and result[-1] == w:
                continue
            result.append(w)
        return result

    def _is_garbage(self, word):
        # very short words
        if len(word) <= 2:
            return True

        # repeated characters like ههههههه
        if len(set(word)) <= 2:
            return True

        return False

    # ------------------------------------------------------------------ #
    #  Output formatting (Tarteel-style)
    # ------------------------------------------------------------------ #

    def _format_results(self, results):
        formatted = []

        for r in results:
            status = r["status"]

            if status == "correct":
                formatted.append({
                    "spoken": r["spoken"],
                    "expected": r["expected"],
                    "status": "correct",
                    "message": "✅ Correct"
                })

            elif status == "incorrect":
                formatted.append({
                    "spoken": r["spoken"],
                    "expected": r["expected"],
                    "status": "incorrect",
                    "message": f"❌ Incorrect — expected: {r['expected']}"
                })

            elif status == "partial":
                formatted.append({
                    "spoken": r["spoken"],
                    "expected": r["expected"],
                    "status": "partial",
                    "message": f"⚠️ Almost — minor diacritic slip: {r['expected']}"
                })

            elif status == "extra":
                formatted.append({
                    "spoken": r["spoken"],
                    # Preserve the engine's contextual expected word (the word at the
                    # pointer when this extra occurred). Only fall back to "-" when the
                    # engine had none (extra spoken past the end of the ayah).
                    "expected": r.get("expected") or "-",
                    "status": "extra",
                    "message": f"🚨 Extra word: {r['spoken']}"
                })

            elif status == "jump":
                formatted.append({
                    "spoken": r["spoken"],
                    "expected": "-",
                    "status": "jump",
                    "missed_words": r["missed"],
                    "message": f"⏭ Skipped: {' '.join(r['missed'])}"
                })

            elif status == "missed":
                formatted.append({
                    "spoken": "-",
                    "expected": r["expected"],
                    "status": "missed",
                    "message": f"⚠️ Missed: {r['expected']}"
                })

        return formatted

    # ------------------------------------------------------------------ #
    #  Statistics tracking
    # ------------------------------------------------------------------ #

    def get_stats(self) -> dict:
        """Return session statistics as a dictionary for the API."""
        return {
            "total": self._stats.total_words,
            "correct": self._stats.correct_words,
            "missed": self._stats.missed_words,
            "incorrect": self._stats.incorrect_words,
            "accuracy": self._stats.accuracy,
        }

    def record_result(self, results: list[dict]):
        """Update statistics based on validation results."""
        for r in results:
            status = r.get("status", "")
            
            if status == "correct":
                self._stats.total_words += 1
                self._stats.correct_words += 1
            elif status == "missed":
                self._stats.total_words += 1
                self._stats.missed_words += 1
            elif status == "incorrect":
                self._stats.total_words += 1
                self._stats.incorrect_words += 1
            elif status == "extra":
                # Extra words don't count against total
                pass
            elif status == "jump":
                # Missed words from jump already counted individually
                missed = r.get("missed", [])
                for _ in missed:
                    self._stats.total_words += 1
                    self._stats.missed_words += 1
            elif status == "empty":
                # Empty transcripts don't count as attempts
                # They're just silence, not a validation attempt
                pass
            elif status == "detection_failed":
                # Detection failures don't count as word attempts
                pass