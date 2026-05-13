# app/core/sequence_engine_v2.py

import time
from typing import List, Dict, Optional
from app.nlp.matcher import best_match_in_window, similarity_score
from app.utils.logger import get_logger

logger = get_logger(__name__)


class QuranNavigator:
    def __init__(self, quran_data, surah_number: int = 1):
        self.quran = quran_data
        self.current_ayah_index = 0
        self.current_word_index = 0
        self.locked_surah = surah_number  # locked from initialization


class SequenceEngineV2:
    def __init__(self, quran_data, state_manager=None, surah_number: int = 1):
        self.quran_data = quran_data
        self.state_manager = state_manager
        self.surah_number = surah_number
        
        # For backward compatibility, maintain navigator
        # but use state_manager as primary if available
        self.navigator = QuranNavigator(quran_data, surah_number=surah_number)
        
        # Store confidence data for current chunk (optional)
        self._word_confidences: Dict[str, float] = {}

    # -----------------------------
    # Public API
    # -----------------------------
    def process(
        self,
        raw_words: List[str],
        filtered_words: List[str],
        word_confidences: Optional[List[Dict]] = None
    ) -> List[Dict]:
        """
        RAW words → jump detection
        FILTERED words → alignment
        WORD_CONFIDENCES → threshold adjustment (optional)
        
        Args:
            raw_words: All transcribed words (for jump detection)
            filtered_words: High-confidence words only (for alignment)
            word_confidences: List of {"word": str, "confidence": float} (optional)
        """
        start = time.perf_counter()

        # Store confidence data for use in matching
        if word_confidences:
            self._word_confidences = {w["word"]: w["confidence"] for w in word_confidences}
        else:
            self._word_confidences = {}

        # 🔥 remove repetition only on filtered words
        filtered_words = self._remove_repetition(filtered_words)

        result = self._process_chunk(raw_words, filtered_words)
        
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"[PERF] sequence_engine.process took {elapsed_ms:.2f} ms")
        
        return result

    # -----------------------------
    # 🔥 CORE ENGINE
    # -----------------------------
    def _process_chunk(self, raw_words: List[str], spoken_words: List[str]) -> List[Dict]:
        chunk_start = time.perf_counter()
        results = []

        # Get current position from state_manager if available, else navigator
        if self.state_manager:
            state = self.state_manager.get_state()
            current_ayah_idx = state.ayah_index
            current_word_idx = state.word_index
        else:
            current_ayah_idx = self.navigator.current_ayah_index
            current_word_idx = self.navigator.current_word_index

        # =========================================================
        # 🔥 GLOBAL JUMP DETECTION (USES RAW WORDS)
        # Only search within locked surah
        # CRITICAL: Only jump if we didn't partially match current ayah!
        # =========================================================
        target_ayah, match_len, score = self._find_best_ayah_match(raw_words, current_ayah_idx)
        
        # Check if we have ANY match in the current ayah FIRST
        # before considering jumps to other ayahs
        current_ayah_words = self._get_current_ayah_words_at(current_ayah_idx)
        has_current_ayah_match = False
        
        if len(raw_words) > 0:
            for spoken_word in raw_words:
                spoken_norm = self._normalize(spoken_word)
                for expected_word in current_ayah_words:
                    expected_norm = self._normalize(expected_word)
                    if similarity_score(spoken_norm, expected_norm) >= 0.70:
                        has_current_ayah_match = True
                        break
                if has_current_ayah_match:
                    break

        if (
            target_ayah != -1
            and target_ayah != current_ayah_idx
            and match_len >= 2
            and score >= 0.80
            and not has_current_ayah_match  # ← CRITICAL: Only jump if NO current ayah match!
        ):
            missed = []

            for i in range(current_ayah_idx, target_ayah):
                missed.extend(self.quran_data[i]["words"])

            logger.debug(
                f"Global jump detected: {match_len} words matched in Ayah {target_ayah} "
                f"(score={score:.2f}), jumping from Ayah {current_ayah_idx}"
            )

            # Jump to target ayah, reset word index
            if self.state_manager:
                self.state_manager.seek(ayah_index=target_ayah, word_index=0)
                current_ayah_idx = target_ayah
                current_word_idx = 0
            else:
                self.navigator.current_ayah_index = target_ayah
                self.navigator.current_word_index = 0
                current_ayah_idx = target_ayah
                current_word_idx = 0

            results.append({
                "spoken": " ".join(raw_words),
                "expected": "-",
                "status": "jump",
                "missed": missed
            })
        elif has_current_ayah_match or (target_ayah == -1):
            # No jump - either we have matches in current ayah or no jump target found
            logger.debug(f"No jump: current_ayah_match={has_current_ayah_match}, target_ayah={target_ayah}")

        # =========================================================
        # 🔥 ALIGNMENT (USES FILTERED WORDS)
        # =========================================================
        current_ayah = self._get_current_ayah_words_at(current_ayah_idx)
        idx = current_word_idx
        expected_sequence = current_ayah[idx:]

        i = 0  # spoken pointer
        j = 0  # expected pointer

        while i < len(spoken_words) and j < len(expected_sequence):
            spoken = spoken_words[i]
            spoken_n = self._normalize(spoken)
            adaptive_threshold = self._get_adaptive_threshold(spoken)

            # ============================================================
            # PRIORITY 1: Try matching at current expected pointer
            # ============================================================
            current_expected = self._normalize(expected_sequence[j])
            current_score = similarity_score(spoken_n, current_expected)
            
            if current_score >= adaptive_threshold:
                # Match at current position ✅
                results.append({
                    "spoken": spoken,
                    "expected": expected_sequence[j],
                    "status": "correct"
                })
                j += 1
                i += 1
                continue

            # ============================================================
            # PRIORITY 2: Try same-ayah lookahead (skip + resume recovery)
            # ============================================================
            # Search ahead in the remaining expected sequence (same ayah)
            # This handles: user skips first words, resumes later in same ayah
            lookahead_window_size = 5  # Look up to 5 words ahead
            best_lookahead_match = self._find_best_lookahead_match(
                spoken_n,
                expected_sequence[j:j+lookahead_window_size],
                adaptive_threshold
            )
            
            if best_lookahead_match is not None:
                # Found a match ahead in the same ayah
                match_idx, match_score = best_lookahead_match
                
                # Mark intermediate expected words as missed
                for skip_idx in range(match_idx):
                    results.append({
                        "spoken": "-",
                        "expected": expected_sequence[j + skip_idx],
                        "status": "missed"
                    })
                
                # Align the spoken word to the matched lookahead word
                results.append({
                    "spoken": spoken,
                    "expected": expected_sequence[j + match_idx],
                    "status": "correct"
                })
                
                # Advance pointer past the matched word
                j += match_idx + 1
                i += 1
                logger.debug(
                    f"Same-ayah lookahead: skipped {match_idx} word(s), "
                    f"aligned '{spoken}' to '{expected_sequence[j-1]}'"
                )
                continue

            # ============================================================
            # PRIORITY 3: Try next-ayah jump detection
            # ============================================================
            # (Jump detection already handled above, this is for inter-ayah recovery)
            # Only try if we're near the end of current ayah
            if j >= len(expected_sequence) - 2:
                # We're near the end, check if user jumped to next ayah
                next_ayah_match = self._find_next_ayah_match(
                    spoken_n,
                    current_ayah_idx,
                    adaptive_threshold
                )
                if next_ayah_match is not None:
                    # Jump detected, let main jump detection handle it
                    # For now, mark as extra to preserve alignment
                    pass

            # ============================================================
            # PRIORITY 4: Mark as extra (no match found anywhere)
            # ============================================================
            # Include what was expected at this position so user knows context
            next_expected = expected_sequence[j] if j < len(expected_sequence) else None
            results.append({
                "spoken": spoken,
                "expected": next_expected,  # Show what was expected at this position
                "status": "extra"
            })
            i += 1

        # Remaining expected words are "not yet spoken in this chunk"
        # IMPORTANT: Do NOT report them as "missed" to the UI in real-time
        # This prevents noisy false positives when the user continues recitation
        # Only track them internally for debugging, but don't send to results
        # CRITICAL: Do NOT increment j here - j should represent actual matches only!
        remaining_idx = j
        while remaining_idx < len(expected_sequence):
            # Internal tracking (for logging only, not sent to client)
            logger.debug(f"Not yet spoken: {expected_sequence[remaining_idx]}")
            remaining_idx += 1

        # =========================================================
        # 🔥 STATE UPDATE - CRITICAL FIX (WITH NO-MATCH CHECK)
        # =========================================================
        # The 'j' pointer has been advanced through the entire alignment loop
        # It represents the actual position we've matched to in the expected sequence
        # 
        # CRITICAL: If j=0, NO words were matched at all!
        # Don't advance pointer - user should retry the same ayah
        
        logger.debug(f"Alignment complete: j={j}, current_word_idx={current_word_idx}")
        logger.debug(f"Ayah {current_ayah_idx} has {len(self._get_current_ayah_words_at(current_ayah_idx))} words")
        
        # Count correct matches vs extra words
        correct_count = sum(1 for r in results if r.get("status") == "correct")
        extra_count = sum(1 for r in results if r.get("status") == "extra")
        
        logger.debug(f"Match summary: {correct_count} correct, {extra_count} extra")
        
        # CRITICAL FIX: If we got mostly EXTRA words (hallucinations), don't advance
        # This prevents false surah completion when Whisper hallucinates
        if extra_count > correct_count:
            logger.debug(
                f"More extra words ({extra_count}) than correct ({correct_count}). "
                f"Likely Whisper hallucinations. Not advancing pointer."
            )
            return results
        
        # If no words matched (j=0), don't advance pointer at all
        if j == 0:
            logger.debug(f"No words matched (j=0). Pointer stays at Ayah {current_ayah_idx}, Word {current_word_idx}")
            logger.debug(f"User should retry the same ayah: {self._get_current_ayah_words_at(current_ayah_idx)}")
            return results
        
        # Words were matched, advance pointer
        new_word_idx = current_word_idx + j
        current_ayah = self._get_current_ayah_words_at(current_ayah_idx)
        
        logger.debug(f"Words matched: j={j}")
        logger.debug(f"New position: word {new_word_idx}")
        
        if self.state_manager:
            # Check if we've reached or passed the end of current ayah
            if new_word_idx >= len(current_ayah):
                # Move to next ayah, word 0 (if it exists)
                next_ayah_idx = current_ayah_idx + 1
                if next_ayah_idx < len(self.quran_data):
                    self.state_manager.seek(ayah_index=next_ayah_idx, word_index=0)
                    logger.debug(f"Reached end of ayah, moving to Ayah {next_ayah_idx}, Word 0")
                else:
                    # Surah complete - move to end of last word
                    last_ayah_idx = len(self.quran_data) - 1
                    last_ayah = self._get_current_ayah_words_at(last_ayah_idx)
                    last_word_idx = len(last_ayah) - 1
                    self.state_manager.seek(ayah_index=last_ayah_idx, word_index=last_word_idx)
                    logger.debug(f"Surah complete - moved to last word")
            else:
                # Stay in current ayah at new position
                self.state_manager.seek(ayah_index=current_ayah_idx, word_index=new_word_idx)
                logger.debug(f"Updated position to Ayah {current_ayah_idx}, Word {new_word_idx}")
        else:
            # Update navigator pointer
            if new_word_idx >= len(current_ayah):
                next_ayah_idx = current_ayah_idx + 1
                if next_ayah_idx < len(self.quran_data):
                    self.navigator.current_ayah_index = next_ayah_idx
                    self.navigator.current_word_index = 0
                else:
                    # Surah complete
                    self.navigator.current_ayah_index = len(self.quran_data) - 1
                    self.navigator.current_word_index = len(self._get_current_ayah_words()) - 1
            else:
                self.navigator.current_word_index = new_word_idx

        chunk_elapsed_ms = (time.perf_counter() - chunk_start) * 1000
        print(f"[PERF] _process_chunk took {chunk_elapsed_ms:.2f} ms")
        
        return results

    # ============================================================
    # 🔍 SAME-AYAH LOOKAHEAD MATCHING (NEW)
    # ============================================================
    def _find_best_lookahead_match(
        self,
        spoken_normalized: str,
        expected_window: List[str],
        threshold: float
    ) -> Optional[tuple]:
        """
        Find the best fuzzy match for a spoken word within a lookahead window.
        
        This implements same-ayah forward recovery:
        If user skips first few words and resumes later in same ayah,
        this finds where they actually are.
        
        Args:
            spoken_normalized: The spoken word (already normalized)
            expected_window: List of expected words to search (same ayah)
            threshold: Minimum similarity score to match
        
        Returns:
            (match_index, score) if found, None otherwise
            match_index is relative to the start of expected_window
        """
        best_idx = None
        best_score = -1
        
        for idx, expected_word in enumerate(expected_window):
            expected_norm = self._normalize(expected_word)
            score = similarity_score(spoken_normalized, expected_norm)
            
            if score >= threshold and score > best_score:
                best_score = score
                best_idx = idx
        
        if best_idx is not None:
            return (best_idx, best_score)
        
        return None
    
    def _find_next_ayah_match(
        self,
        spoken_normalized: str,
        current_ayah_idx: int,
        threshold: float
    ) -> Optional[int]:
        """
        Check if spoken word matches the first word of the next ayah.
        
        This is for detecting same-word-based ayah transitions.
        
        Args:
            spoken_normalized: The spoken word (already normalized)
            current_ayah_idx: Current ayah index
            threshold: Minimum similarity score
        
        Returns:
            Next ayah index if match found, None otherwise
        """
        if current_ayah_idx + 1 >= len(self.quran_data):
            return None
        
        next_ayah_words = self.quran_data[current_ayah_idx + 1]["words"]
        if not next_ayah_words:
            return None
        
        next_first_word = self._normalize(next_ayah_words[0])
        score = similarity_score(spoken_normalized, next_first_word)
        
        if score >= threshold:
            return current_ayah_idx + 1
        
        return None

    # ============================================================
    # 🔍 GLOBAL MATCHING (within locked Surah)
    # ============================================================
    def _find_best_ayah_match(self, spoken_words: List[str], current_ayah_idx: int = 0):
        """
        Find best matching ayah within lookahead range.
        Search is limited to current_ayah_idx onwards (same surah lock).
        """
        best_ayah_idx = -1
        best_score = 0
        best_match_len = 0

        # Only search within lookahead distance from current position
        # This ensures we stay within the same surah
        search_start = current_ayah_idx
        search_end = min(current_ayah_idx + 5, len(self.quran_data))

        for i in range(search_start, search_end):
            ayah_words = self.quran_data[i]["words"]

            score = 0
            match_len = 0

            for j in range(min(len(spoken_words), len(ayah_words))):
                s = self._normalize(spoken_words[j])
                e = self._normalize(ayah_words[j])

                sim = similarity_score(s, e)

                if sim >= 0.75:
                    score += sim
                    match_len += 1
                else:
                    if match_len >= 2:
                        break
                    continue

            if match_len > 0:
                avg_score = score / match_len

                if (
                    match_len > best_match_len or
                    (match_len == best_match_len and avg_score > best_score)
                ):
                    best_score = avg_score
                    best_ayah_idx = i
                    best_match_len = match_len

        return best_ayah_idx, best_match_len, best_score

    # -----------------------------
    # Helpers
    # -----------------------------
    def _get_confidence_for_word(self, word: str) -> float:
        """Get confidence score for a word (0.0-1.0), default 0.65 if unknown."""
        return self._word_confidences.get(word, 0.65)
    
    def _get_adaptive_threshold(self, word: str) -> float:
        """
        Compute adaptive match threshold based on word confidence.
        
        CRITICAL LOGIC:
        - High confidence (0.85+) → LOOSE threshold (0.75) - trust Whisper
        - Medium confidence (0.65-0.84) → NORMAL threshold (0.80)
        - Low confidence (0.45-0.64) → STRICT threshold (0.85) - prevent false matches
        - Very low (<0.45) → VERY STRICT (0.90) - require near-exact match
        
        This prevents low-confidence words from matching incorrectly.
        Example: "مالذراي" (conf 0.07) uses 0.90 threshold, won't match "الرَّحِيمِ"
        """
        conf = self._get_confidence_for_word(word)
        
        if conf >= 0.85:
            return 0.75  # High confidence - lenient (trust Whisper)
        elif conf >= 0.65:
            return 0.80  # Medium confidence - normal
        elif conf >= 0.45:
            return 0.85  # Low confidence - STRICT (prevent false matches)
        else:
            return 0.90  # Very low confidence - VERY STRICT (near-exact only)

    def _get_current_ayah_words(self):
        """Get words from current ayah (using navigator for backward compat)."""
        if self.navigator.current_ayah_index >= len(self.quran_data):
            return []
        return self.quran_data[self.navigator.current_ayah_index]["words"]

    def _get_current_ayah_words_at(self, ayah_index: int):
        """Get words from specific ayah index."""
        if ayah_index >= len(self.quran_data):
            return []
        return self.quran_data[ayah_index]["words"]

    def _end_of_ayah(self):
        """Check if at end of ayah (using navigator for backward compat)."""
        ayah = self._get_current_ayah_words()
        return self.navigator.current_word_index >= len(ayah)

    def _normalize(self, word: str) -> str:
        """
        Normalize Arabic word by:
        1. Removing diacritics (fatha, damma, kasra, sukun, etc.)
        2. Removing prefix 'و' (conjunction)
        
        Examples:
            الْحَمْدُ → الحمد
            والرَّحِمَن → الرحمن
        """
        # Remove all Arabic diacritical marks
        # Unicode ranges for Arabic diacritics:
        # \u064B-\u0652: Fathatan, Dammatan, Fatha, Damma, Kasra, Sukun, Shadda, etc.
        # \u0670: Alef above
        # \u0640: Tatweel
        diacritics = "\u064B\u064C\u064D\u064E\u064F\u0650\u0651\u0652\u0670\u0640"
        for diacritic in diacritics:
            word = word.replace(diacritic, "")
        
        # Remove prefix 'و' (and/with)
        if word.startswith("و") and len(word) > 2:
            word = word[1:]
        
        return word

    def _remove_repetition(self, words: List[str]) -> List[str]:
        """Prevent repetition like الرحمن الرحمن الرحمن..."""
        result = []
        for w in words:
            if len(result) >= 2 and result[-1] == result[-2] == w:
                continue
            result.append(w)
        return result