# app/stt/whisper_engine.py

import time
import numpy as np
from faster_whisper import WhisperModel
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Base Quranic vocabulary prompt — primes Whisper to expect Quranic Arabic.
# This is the single biggest accuracy improvement over generic Arabic STT.
QURAN_PROMPT = (
    "بسم الله الرحمن الرحيم الحمد لله رب العالمين الرحمن الرحيم "
    "مالك يوم الدين إياك نعبد وإياك نستعين اهدنا الصراط المستقيم "
    "صراط الذين أنعمت عليهم غير المغضوب عليهم ولا الضالين"
)

# Whisper word-level confidence below this is flagged as uncertain.
# Words below this threshold are included in the transcript but marked
# so the sequence engine can apply a looser match threshold for them.
LOW_CONFIDENCE_CUTOFF = 0.55


class WhisperEngine:

    def __init__(self, model_size: str = "small"):
        """
        model_size options (accuracy vs speed tradeoff):
            "base"     — fastest, weakest Arabic accuracy  (not recommended)
            "small"    — good balance for development
            "medium"   — recommended for production
            "large-v3" — best Arabic accuracy, needs GPU for real-time use
        """
        logger.info(f"Loading Faster Whisper [{model_size}]...")
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")
        self.model_size = model_size
        self._last_word_confidences: list[dict] = []
        logger.info("Faster Whisper ready")

    def transcribe(self, audio: np.ndarray, expected_context: str = "") -> str:
        """
        Transcribe a float32 16kHz audio array with Quranic context priming.

        Args:
            audio:            float32 numpy array at 16kHz
            expected_context: next few expected words from the Quran text.
                              Placed at the front of the prompt so Whisper
                              sees the exact vocabulary it should produce.
        Returns:
            Transcribed Arabic text string (full text, not filtered).
            Per-word confidence scores accessible via get_last_word_confidences().
        """
        start = time.perf_counter()
        
        # Build prompt: expected words first, then base Quran vocabulary
        # prompt = f"{expected_context} {QURAN_PROMPT}".strip() if expected_context else QURAN_PROMPT
        prompt = expected_context if expected_context else QURAN_PROMPT
        segments, _ = self.model.transcribe(
            audio,
            language="ar",
            initial_prompt=prompt,
            beam_size=3,
            best_of=3,
            temperature=0.2,                  # greedy — stops hallucinations
            condition_on_previous_text=False,  # prevents drift across loop iterations
            word_timestamps=True,              # required for per-word confidence
            vad_filter=True,                   # skip silent audio segments
            vad_parameters={
                "min_silence_duration_ms": 500,
                "speech_pad_ms": 200,
            },
            no_speech_threshold=0.4,
            compression_ratio_threshold=2.0,
        )

        full_text = ""
        word_confidences = []

        for segment in segments:
            full_text += segment.text + " "

            if segment.words:
                for w in segment.words:
                    # Skip garbage tokens before recording
                    if self._is_garbage_token(w.word):
                        logger.debug(f"Filtered garbage token: '{w.word}'")
                        continue
                    
                    word_confidences.append({
                        "word": w.word.strip(),
                        "confidence": round(w.probability, 3),
                        "low_confidence": w.probability < LOW_CONFIDENCE_CUTOFF,
                    })

        self._last_word_confidences = word_confidences
        transcript = full_text.strip()

        # Log any low-confidence words so they're visible during debugging
        low_conf = [w for w in word_confidences if w["low_confidence"]]
        if low_conf:
            logger.debug(
                f"Low-confidence words: "
                + ", ".join(f"{w['word']}({w['confidence']:.2f})" for w in low_conf)
            )

        logger.debug(f"Transcript: '{transcript}'")
        #new
        transcript = self.remove_repetition(transcript)
        
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"[PERF] transcribe took {elapsed_ms:.2f} ms")
        
        return transcript

    def transcribe_filtered(self, audio: np.ndarray, expected_context: str = "") -> str:
        """
        Transcribe and drop any word with confidence below LOW_CONFIDENCE_CUTOFF.
        Useful when you want a cleaner (shorter) string — trades recall for precision.
        Use this when false positives are causing more problems than missed words.
        """
        self.transcribe(audio, expected_context)
        trusted = [
            w["word"] for w in self._last_word_confidences
            if not w["low_confidence"]
        ]
        return " ".join(trusted)

    def get_last_word_confidences(self) -> list[dict]:
        """
        Return per-word confidence scores from the most recent transcription.
        Format: [{"word": str, "confidence": float, "low_confidence": bool}, ...]
        Feed these into SequenceEngine.set_word_confidences() so it can apply
        looser matching thresholds for uncertain words.
        """
        return self._last_word_confidences

    def get_model(self):
        """
        Get the underlying Whisper model instance.
        Used for pre-loading the model in main thread to avoid OpenMP conflicts.
        """
        return self.model


    def remove_repetition(self, text):
        words = text.split()
        result = []

        for w in words:
            if len(result) >= 2 and result[-1] == result[-2] == w:
                continue
            result.append(w)

        return " ".join(result)

    def _is_garbage_token(self, word: str, max_char_repeat: int = 3) -> bool:
        """
        Detect pathological tokens like 'اتِيييييي' or 'هههههههه'.
        
        DO NOT filter valid Quranic words with diacritics.
        Only filter actual garbage (excessive repetition, no actual letters, etc).
        """
        if not word:
            return True
        
        # First, check if this looks like a real Arabic word
        # Arabic Unicode ranges: 0x0600-0x06FF (general), 0x0750-0x077F (extended)
        has_arabic_letter = any(0x0600 <= ord(c) <= 0x06FF or 0x0750 <= ord(c) <= 0x077F 
                                for c in word)
        
        if not has_arabic_letter:
            logger.debug(f"Filtered: no Arabic letters in '{word}'")
            return True
        
        # Check for excessive character repetition (actual garbage)
        # e.g., "اتِيييييي" has 6 consecutive "ي"
        for i in range(len(word) - max_char_repeat):
            substring = word[i:i + max_char_repeat + 1]
            # If more than max_char_repeat identical consecutive chars
            if len(set(substring)) == 1:
                logger.debug(f"Filtered: excessive repetition in '{word}'")
                return True
        
        # Otherwise, keep it (even if it has low confidence)
        return False