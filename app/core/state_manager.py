# app/core/state_manager.py

from app.core.models import State, Ayah
from typing import List


class StateManager:

    def __init__(self, surah: int, ayahs: List[Ayah]):
        self.ayahs = ayahs
        self.state = State(
            surah=surah,
            ayah_index=0,
            word_index=0
        )
        self._snapshot: State | None = None

    # ------------------------------------------------------------------ #
    #  Word access
    # ------------------------------------------------------------------ #

    def get_current_word(self) -> str:
        return self._current_ayah().words[self.state.word_index].text

    def get_current_word_normalized(self) -> str:
        return self._current_ayah().words[self.state.word_index].normalized

    def get_current_ayah_number(self) -> int:
        return self._current_ayah().ayah_number

    def get_current_position(self) -> dict:
        return {
            "ayah_index": self.state.ayah_index,
            "word_index": self.state.word_index,
            "ayah_number": self.get_current_ayah_number(),
            "word": self.get_current_word(),
        }

    # ------------------------------------------------------------------ #
    #  Cursor movement
    # ------------------------------------------------------------------ #

    def move_next_word(self):
        ayah = self._current_ayah()
        if self.state.word_index < len(ayah.words) - 1:
            self.state.word_index += 1
        else:
            self._move_next_ayah()

    def _move_next_ayah(self):
        if self.state.ayah_index < len(self.ayahs) - 1:
            self.state.ayah_index += 1
            self.state.word_index = 0

    def seek(self, ayah_index: int, word_index: int):
        """Jump the cursor to a specific ayah + word position."""
        if ayah_index < 0 or ayah_index >= len(self.ayahs):
            raise ValueError(f"ayah_index {ayah_index} out of range")
        ayah = self.ayahs[ayah_index]
        if word_index < 0 or word_index >= len(ayah.words):
            raise ValueError(f"word_index {word_index} out of range for ayah {ayah_index}")
        self.state.ayah_index = ayah_index
        self.state.word_index = word_index

    # ------------------------------------------------------------------ #
    #  Snapshot / restore  (used when blocking user on incorrect word)
    # ------------------------------------------------------------------ #

    def save_snapshot(self):
        """Save current cursor position so it can be restored on retry."""
        self._snapshot = State(
            surah=self.state.surah,
            ayah_index=self.state.ayah_index,
            word_index=self.state.word_index,
        )

    def restore_snapshot(self):
        """Restore cursor to the last saved snapshot (retry same word)."""
        if self._snapshot is not None:
            self.state.ayah_index = self._snapshot.ayah_index
            self.state.word_index = self._snapshot.word_index

    # ------------------------------------------------------------------ #
    #  State queries
    # ------------------------------------------------------------------ #

    def get_state(self) -> State:
        return self.state

    def is_finished(self) -> bool:
        last_ayah = self.ayahs[-1]
        return (
            self.state.ayah_index == len(self.ayahs) - 1
            and self.state.word_index >= len(last_ayah.words) - 1
        )

    def total_words(self) -> int:
        return sum(len(a.words) for a in self.ayahs)

    def words_completed(self) -> int:
        completed = sum(len(self.ayahs[i].words) for i in range(self.state.ayah_index))
        completed += self.state.word_index
        return completed

    def progress_percent(self) -> float:
        total = self.total_words()
        if total == 0:
            return 0.0
        return round(self.words_completed() / total * 100, 1)

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _current_ayah(self) -> Ayah:
        return self.ayahs[self.state.ayah_index]
