# app/core/models.py

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Word:
    text: str
    normalized: str


@dataclass
class Ayah:
    ayah_number: int
    words: List[Word]


@dataclass
class State:
    surah: int
    ayah_index: int
    word_index: int


@dataclass
class ValidationResult:
    is_correct: bool
    error_type: Optional[str] = None          # None | "missed" | "incorrect" | "empty"
    expected_word: Optional[str] = None       # the word(s) that were expected
    spoken_word: Optional[str] = None         # what the user actually said
    similarity_score: Optional[float] = None  # 0.0 – 1.0
    missed_words: List[str] = field(default_factory=list)  # list of skipped words


@dataclass
class SessionStats:
    total_words: int = 0
    correct_words: int = 0
    missed_words: int = 0
    incorrect_words: int = 0

    @property
    def accuracy(self) -> float:
        if self.total_words == 0:
            return 0.0
        return round(self.correct_words / self.total_words * 100, 1)
