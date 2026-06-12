# app/nlp/normalizer.py

import unicodedata
import re


# Standard Arabic harakat (diacritics / tashkeel)
ARABIC_DIACRITICS = set([
    '\u0610', '\u0611', '\u0612', '\u0613', '\u0614',
    '\u0615', '\u0616', '\u0617', '\u0618', '\u0619',
    '\u061A', '\u064B', '\u064C', '\u064D', '\u064E',
    '\u064F', '\u0650', '\u0651', '\u0652', '\u0653',
    '\u0654', '\u0655', '\u0656', '\u0657', '\u0658',
    '\u0659', '\u065A', '\u065B', '\u065C', '\u065D',
    '\u065E', '\u065F',
])

# Uthmani script special symbols found in the Quran mushaf
# These never appear in a spoken transcript so must be stripped from expected text
UTHMANI_SPECIAL = set([
    '\u06D6', '\u06D7', '\u06D8', '\u06D9', '\u06DA',
    '\u06DB', '\u06DC', '\u06DD', '\u06DE', '\u06DF',
    '\u06E0', '\u06E1', '\u06E2', '\u06E3', '\u06E4',
    '\u06E5', '\u06E6', '\u06E7', '\u06E8', '\u06E9',
    '\u06EA', '\u06EB', '\u06EC', '\u06ED',
    '\u0600', '\u0601', '\u0602', '\u0603',  # Arabic number signs
    '\u06F0',  # extended Arabic-Indic digits
    '\uFDFD',  # ﷽ (Basmala ligature)
])

# Tatweel (kashida) — decorative elongation character, not phonetic
TATWEEL = '\u0640'

# All characters to strip from both expected and spoken text before comparing
STRIP_CHARS = ARABIC_DIACRITICS | UTHMANI_SPECIAL | {TATWEEL}


def remove_diacritics(text: str) -> str:
    return ''.join(c for c in text if c not in STRIP_CHARS)


def normalize_hamza(text: str) -> str:
    """Unify all alef+hamza variants to bare alef."""
    return (
        text
        .replace('\u0623', '\u0627')  # أ → ا
        .replace('\u0625', '\u0627')  # إ → ا
        .replace('\u0622', '\u0627')  # آ → ا
        .replace('\u0671', '\u0627')  # ٱ (alef wasla) → ا
        .replace('\u0649', '\u064A')  # ى → ي (alef maqsura → ya)
    )


def normalize_ta_marbuta(text: str) -> str:
    """Normalize ta marbuta. Context-sensitive: at end of word → ha."""
    return text.replace('\u0629', '\u0647')  # ة → ه


def normalize_waw(text: str) -> str:
    """Normalize waw with hamza above."""
    return text.replace('\u0624', '\u0648')  # ؤ → و


def normalize_ya(text: str) -> str:
    """Normalize ya with hamza below."""
    return text.replace('\u0626', '\u064A')  # ئ → ي


def normalize_arabic(text: str) -> str:
    """
    Full normalization pipeline for Arabic Quranic text.
    Safe to apply to BOTH the expected Quran text and the Whisper transcript.
    """
    if not text:
        return ""

    text = unicodedata.normalize('NFC', text)
    text = remove_diacritics(text)
    text = normalize_hamza(text)
    text = normalize_ta_marbuta(text)
    text = normalize_waw(text)
    text = normalize_ya(text)

    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text)

    return text.strip()


def normalize_word(word: str) -> str:
    """Normalize a single word. Same pipeline, no whitespace collapsing needed."""

    return normalize_arabic(word)


def strip_diacritics(text: str) -> str:
    """
    Remove ALL Arabic diacritics (harakat, shadda, sukun, tatweel) for
    diacritic-insensitive word lookup. Lets 'بِسْمِ' and 'بسم' resolve equal.

    Distinct from the matcher's normalization, which preserves short vowels
    so vowel errors are detectable — this helper is only for lookups where
    diacritics must be ignored (e.g. mapping an expected word to its ayah).
    """
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(
        ch for ch in nfd
        if not (0x064B <= ord(ch) <= 0x065F) and ord(ch) != 0x0640
    )
    return unicodedata.normalize("NFC", stripped).strip()
