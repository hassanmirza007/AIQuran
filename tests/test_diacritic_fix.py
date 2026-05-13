#!/usr/bin/env python3
"""
Test: Verify diacritic removal in normalization
This tests the fix for the EXTRA/MISSED word issue
"""

from app.core.sequence_engine_v2 import SequenceEngineV2
from app.core.state_manager import StateManager
from app.data.quran_loader import load_surah
from app.core.models import Ayah, Word

# Create a minimal test Quran structure
test_ayah = Ayah(
    ayah_number=1,
    words=[
        Word(text="الْحَمْدُ", normalized="الحمد"),
        Word(text="لِلَّهِ", normalized="لله"),
        Word(text="رَبِّ", normalized="رب"),
        Word(text="الْعَالَمِينَ", normalized="العالمين"),
    ]
)

test_quran_data = [
    {
        "ayah": 1,
        "words": ["الْحَمْدُ", "لِلَّهِ", "رَبِّ", "الْعَالَمِينَ"]
    }
]

# Create engine
engine = SequenceEngineV2(test_quran_data, state_manager=None)

# Test 1: Normalization removes diacritics
print("=" * 60)
print("TEST 1: Diacritic Removal")
print("=" * 60)

test_cases = [
    ("الْحَمْدُ", "الحمد", "Should remove diacritics"),
    ("لِلَّهِ", "لله", "Should remove diacritics + shadda"),
    ("رَبِّ", "رب", "Should remove fatha + shadda"),
    ("الْعَالَمِينَ", "العالمين", "Should remove sukun + diacritics"),
    ("والرَّحِمَن", "الرحمن", "Should remove diacritics + و prefix"),
    ("ألحمد", "الحمد", "Already clean (no diacritics)"),
]

all_passed = True
for word, expected, description in test_cases:
    normalized = engine._normalize(word)
    status = "✅ PASS" if normalized == expected else "❌ FAIL"
    if normalized != expected:
        all_passed = False
    print(f"{status}: {description}")
    print(f"  Input:    {word}")
    print(f"  Expected: {expected}")
    print(f"  Got:      {normalized}")
    print()

print("=" * 60)
print("TEST 2: Similarity Matching After Normalization")
print("=" * 60)

from app.nlp.matcher import similarity_score

# These should now match perfectly
spoken = "ألحمد"  # From Whisper (no diacritics)
expected = "الْحَمْدُ"  # From Quran (with diacritics)

spoken_norm = engine._normalize(spoken)
expected_norm = engine._normalize(expected)

score = similarity_score(spoken_norm, expected_norm)

print(f"Spoken (raw):     {spoken}")
print(f"Spoken (normalized): {spoken_norm}")
print()
print(f"Expected (raw):   {expected}")
print(f"Expected (normalized): {expected_norm}")
print()
print(f"Similarity score: {score:.3f}")
print(f"Threshold: 0.75")
print(f"Match: {'✅ YES' if score >= 0.75 else '❌ NO'}")
print()

if all_passed and score >= 0.75:
    print("=" * 60)
    print("✅ ALL TESTS PASSED!")
    print("=" * 60)
    print("\nThe EXTRA/MISSED issue should now be fixed.")
    print("Run your actual test case to confirm.")
else:
    print("=" * 60)
    print("❌ SOME TESTS FAILED")
    print("=" * 60)
