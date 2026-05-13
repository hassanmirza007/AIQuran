# app/core/validator.py

from app.core.models import ValidationResult
from app.utils.logger import get_logger

logger = get_logger(__name__)

MAX_ATTEMPTS = 5       # force-skip after this many consecutive failures
HINT_AT_ATTEMPT = 3   # show first 2 chars of expected word as hint


class WordValidator:
    """
    Manages the retry-blocking loop for a single expected word.

    Tarteel-like behaviour:
        - User cannot advance until the word is said correctly.
        - After HINT_AT_ATTEMPT failures, a hint is shown (first 2 chars).
        - After MAX_ATTEMPTS failures, the word is force-skipped so the
          user is not permanently stuck.

    This sits above SequenceEngine. SequenceEngine does matching;
    WordValidator decides whether to block, hint, or force-advance.
    """

    def __init__(self):
        self._attempts: dict[str, int] = {}

    def _key(self, ayah_index: int, word_index: int) -> str:
        return f"{ayah_index}:{word_index}"

    def record_attempt(self, ayah_index: int, word_index: int):
        key = self._key(ayah_index, word_index)
        self._attempts[key] = self._attempts.get(key, 0) + 1

    def attempt_count(self, ayah_index: int, word_index: int) -> int:
        return self._attempts.get(self._key(ayah_index, word_index), 0)

    def reset(self, ayah_index: int, word_index: int):
        self._attempts.pop(self._key(ayah_index, word_index), None)

    def should_force_skip(self, ayah_index: int, word_index: int) -> bool:
        return self.attempt_count(ayah_index, word_index) >= MAX_ATTEMPTS

    def should_show_hint(self, ayah_index: int, word_index: int) -> bool:
        return self.attempt_count(ayah_index, word_index) >= HINT_AT_ATTEMPT

    def get_hint(self, expected_word: str) -> str:
        return (expected_word[:2] + "...") if len(expected_word) > 2 else expected_word

    def evaluate(
        self,
        result: ValidationResult,
        ayah_index: int,
        word_index: int,
    ) -> dict:
        """
        Decide what to do given a SequenceEngine ValidationResult.

        Returns:
            action      : "advance" | "block" | "force_skip"
            show_hint   : bool
            hint        : str | None
            attempts    : int
            result      : the original ValidationResult
        """
        if result.is_correct or result.error_type == "missed":
            self.reset(ayah_index, word_index)
            return {
                "action": "advance",
                "show_hint": False,
                "hint": None,
                "attempts": 0,
                "result": result,
            }

        self.record_attempt(ayah_index, word_index)
        attempts = self.attempt_count(ayah_index, word_index)

        if self.should_force_skip(ayah_index, word_index):
            logger.info(f"Force-skip at {ayah_index}:{word_index} after {attempts} attempts")
            self.reset(ayah_index, word_index)
            return {
                "action": "force_skip",
                "show_hint": True,
                "hint": self.get_hint(result.expected_word or ""),
                "attempts": attempts,
                "result": result,
            }

        hint = self.get_hint(result.expected_word or "") if self.should_show_hint(ayah_index, word_index) else None

        return {
            "action": "block",
            "show_hint": hint is not None,
            "hint": hint,
            "attempts": attempts,
            "result": result,
        }
