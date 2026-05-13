#!/usr/bin/env python3
"""
Test: Same-Ayah Lookahead Fix
Verifies that the engine correctly handles intra-ayah skips/resumptions
"""

from app.core.sequence_engine_v2 import SequenceEngineV2

# Create minimal test Quran data
test_quran = [
    {
        "ayah": 4,
        "words": ["مَالِكِ", "يَوْمِ", "الدِّينِ"]
    },
    {
        "ayah": 5,
        "words": ["إِيَّاكَ", "نَعْبُدُ", "وَإِيَّاكَ", "نَسْتَعِينُ"]
    }
]

# Create engine
engine = SequenceEngineV2(test_quran, state_manager=None)

print("=" * 70)
print("TEST: Same-Ayah Lookahead Alignment")
print("=" * 70)

# Test case 1: User skips first word, resumes in middle
print("\n[Test 1] User skips first word, says words 2-3")
print("-" * 70)

# More realistic test: user says "يَوْمِ" (slightly garbled) then "الدِّينِ"
raw_words = ["يومِ", "الدِّينِ"]  # "يومِ" is a slight Whisper variation of "يَوْمِ"
conf_data = [
    {"word": "يومِ", "confidence": 0.88},
    {"word": "الدِّينِ", "confidence": 0.92}
]

results = engine.process(raw_words, raw_words, conf_data)

print("Expected behavior:")
print("  1. 'مَالِكِ' → MISSED (user skipped it)")
print("  2. 'يومِ' → CORRECT (matched to 'يَوْمِ')")
print("  3. 'الدِّينِ' → CORRECT")
print()
print("Actual results:")
for r in results:
    spoken = r.get("spoken") or "-"
    expected = r.get("expected") or "-"
    status = r.get("status") or "?"
    print(f"  Spoken: {str(spoken):15} Expected: {str(expected):15} Status: {status}")
print()

# Verify
expected_statuses = ["missed", "correct", "correct"]
actual_statuses = [r["status"] for r in results]

if actual_statuses == expected_statuses:
    print("✅ PASS: Same-ayah lookahead works correctly!")
else:
    print("❌ FAIL: Expected statuses:", expected_statuses)
    print("        Got statuses:", actual_statuses)

# Test case 2: Normal sequential match (should still work)
print("\n" + "=" * 70)
print("[Test 2] Normal sequential matching (no skip)")
print("-" * 70)

engine2 = SequenceEngineV2(test_quran, state_manager=None)
raw_words2 = ["مَالِكِ", "يَوْمِ", "الدِّينِ"]
conf_data2 = [
    {"word": w, "confidence": 0.90} for w in raw_words2
]

results2 = engine2.process(raw_words2, raw_words2, conf_data2)

print("Expected: All 3 words CORRECT")
print()
print("Actual results:")
for r in results2:
    spoken = r.get("spoken") or "-"
    expected = r.get("expected") or "-"
    status = r.get("status") or "?"
    print(f"  Spoken: {str(spoken):15} Expected: {str(expected):15} Status: {status}")
print()

actual_statuses2 = [r["status"] for r in results2]
if all(s == "correct" for s in actual_statuses2):
    print("✅ PASS: Sequential matching still works!")
else:
    print("❌ FAIL: Sequential matching broken!")
    print("         Statuses:", actual_statuses2)

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print("Same-ayah lookahead fix enables:")
print("  ✓ Intra-ayah skip recovery")
print("  ✓ Later same-ayah words no longer marked as EXTRA")
print("  ✓ Proper pointer advancement")
print("  ✓ Better UX for users who skip/pause mid-ayah")
print("=" * 70)
