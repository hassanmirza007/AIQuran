# app/nlp/matcher.py

import jellyfish
from Levenshtein import ratio as levenshtein_ratio


def similarity_score(spoken: str, expected: str) -> float:
    """
    Combined similarity score using Jaro-Winkler and Levenshtein ratio.

    Jaro-Winkler is good at prefix matching (important for Arabic root morphology).
    Levenshtein ratio handles substitutions and short words better.

    We average both to get a more robust score.
    """
    if not spoken or not expected:
        return 0.0

    jw = jellyfish.jaro_winkler_similarity(spoken, expected)
    lev = levenshtein_ratio(spoken, expected)

    # Weighted average: JW slightly favoured for Arabic prefix patterns
    return round((jw * 0.6) + (lev * 0.4), 4)


def is_match(spoken: str, expected: str, threshold: float = 0.82) -> bool:
    """
    Check if spoken word matches expected word.

    Threshold of 0.82 (down from 0.85) accounts for:
    - Whisper occasionally dropping a short vowel sound
    - Slight dialect variation in non-native reciters
    - Normalizer unifying most major variants already
    """
    return similarity_score(spoken, expected) >= threshold


def best_match_in_window(
    spoken_word: str,
    candidates: list[str],
    threshold: float = 0.80,
) -> tuple[int, float]:
    """
    Find the best matching word in a list of candidate words.
    Returns (index, score) of the best match, or (-1, 0.0) if none pass threshold.

    Used by the sequence engine for lookahead alignment.
    """
    best_idx = -1
    best_score = 0.0

    for i, candidate in enumerate(candidates):
        score = similarity_score(spoken_word, candidate)
        if score > best_score:
            best_score = score
            best_idx = i

    if best_score >= threshold:
        return best_idx, best_score

    return -1, best_score
