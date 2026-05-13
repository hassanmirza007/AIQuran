# app/services/start_detector.py

from app.nlp.normalizer import normalize_arabic
from app.nlp.matcher import similarity_score
from typing import Optional


class StartDetector:
    """
    Detects where in the Quran the user has started reciting.

    Uses a sliding window across all loaded ayahs to find the position
    whose word sequence best matches what the user spoke.

    Improvements over original:
    - Returns confidence score alongside position
    - Minimum window size of 2 words to reduce false positives
    - Returns full position metadata for the caller
    - Can be used to auto-lock a session to a detected position
    """

    def __init__(self, ayahs, threshold: float = 0.78, min_words: int = 2):
        self.ayahs = ayahs
        self.threshold = threshold
        self.min_words = min_words  # require at least this many spoken words to detect

    def detect_start(self, spoken_text: str) -> Optional[dict]:
        """
        Detect starting position from spoken text.
        
        Returns:
            dict with keys: ayah_index, word_index, ayah_number, confidence
            or None if confidence below threshold or insufficient words
        """
        spoken_words = [normalize_arabic(w) for w in spoken_text.split() if w.strip()]

        if len(spoken_words) < self.min_words:
            return None

        best_match = None
        best_score = 0.0

        for ayah_index, ayah in enumerate(self.ayahs):
            expected_words = [w.normalized for w in ayah.words]

            for i in range(len(expected_words)):
                window = expected_words[i: i + len(spoken_words)]

                if len(window) != len(spoken_words):
                    continue

                score = self._sequence_similarity(spoken_words, window)

                if score > best_score:
                    best_score = score
                    best_match = {
                        "ayah_index": ayah_index,
                        "word_index": i,
                        "ayah_number": ayah.ayah_number,
                        "confidence": round(score, 3),
                    }

        if best_score >= self.threshold:
            return best_match

        return None

    def _sequence_similarity(self, seq1: list, seq2: list) -> float:
        if not seq1 or not seq2:
            return 0.0
        scores = [similarity_score(a, b) for a, b in zip(seq1, seq2)]
        return sum(scores) / len(scores)
