# app/core/jump_detection.py

"""
Robust jump detection for Quran recitation validation.

Detects when a user jumps ahead to a different ayah,
accounting for confidence and using alignment-based matching.

Key features:
1. Confidence-aware filtering
2. Alignment-based scoring
3. Avoids false positives
4. Handles noisy STT
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from app.core.alignment_engine import AlignmentEngine, ConfidenceData, AlignmentMatch
from app.nlp.matcher import similarity_score


@dataclass
class JumpDetectionResult:
    """Result of jump detection."""
    detected: bool
    target_ayah: int = -1
    match_length: int = 0
    average_score: float = 0.0
    average_confidence: float = 0.0
    alignment: List[AlignmentMatch] = None
    missed_word_count: int = 0
    confidence_metrics: Dict = None


class RobustJumpDetector:
    """
    Detects forward jumps in Quran recitation.
    
    Algorithm:
    1. Filter low-confidence words (unreliable STT)
    2. Search upcoming ayahs within context window
    3. Use alignment to score each candidate
    4. Select best match meeting thresholds
    5. Return with confidence metrics
    
    This replaces the heuristic jump detection in SequenceEngineV2.
    """
    
    def __init__(self, context_window: int = 3):
        """
        Args:
            context_window: Look ahead N ayahs (e.g., 3 = current + 2)
        """
        self.context_window = context_window
        self.alignment_engine = AlignmentEngine()
    
    def detect_jump(
        self,
        spoken_words: List[str],
        spoken_confidences: List[ConfidenceData],
        current_ayah_idx: int,
        quran_data: List[Dict],
        confidence_threshold: float = 0.65,
        min_match_length: int = 2,
        min_average_score: float = 0.80,
        min_average_confidence: float = 0.60
    ) -> JumpDetectionResult:
        """
        Detect if user jumped to a different ayah.
        
        Algorithm:
        1. Pre-filter: Keep only reasonably confident words
        2. Search: Try matching against ayahs in context window
        3. Score: Use alignment to compute match quality
        4. Filter: Keep only matches meeting thresholds
        5. Return: Best match or None
        
        Args:
            spoken_words: Transcribed words
            spoken_confidences: Per-word confidence data
            current_ayah_idx: User's current position
            quran_data: Full Quran data structure
            confidence_threshold: Min confidence to keep word (0-1)
            min_match_length: Min words that must match
            min_average_score: Min average similarity score
            min_average_confidence: Min average confidence in matched words
        
        Returns:
            JumpDetectionResult with detection status and details
        
        Example:
        ```
        result = detector.detect_jump(
            spoken_words=["الحمد", "لله", "رب", "العالمين"],
            spoken_confidences=[conf1, conf2, conf3, conf4],
            current_ayah_idx=0,
            quran_data=quran,
            confidence_threshold=0.60
        )
        
        if result.detected:
            print(f"User jumped to Ayah {result.target_ayah}")
            print(f"Match quality: {result.average_score:.2f}")
        ```
        """
        
        # ============================================================
        # Step 1: Pre-filter (keep reasonably confident words)
        # ============================================================
        high_conf_indices = []
        filtered_words = []
        filtered_confidences = []
        
        for i, (word, conf) in enumerate(zip(spoken_words, spoken_confidences)):
            if conf.confidence >= confidence_threshold:
                high_conf_indices.append(i)
                filtered_words.append(word)
                filtered_confidences.append(conf)
        
        # Need at least min_match_length words to detect a jump
        if len(filtered_words) < min_match_length:
            return JumpDetectionResult(
                detected=False,
                confidence_metrics={
                    "reason": "Insufficient high-confidence words",
                    "filtered_word_count": len(filtered_words),
                    "required": min_match_length
                }
            )
        
        # ============================================================
        # Step 2: Search within context window
        # ============================================================
        best_result = None
        best_cost = float('inf')
        search_results = []
        
        # Search from next ayah to context_window boundary
        search_start = current_ayah_idx + 1
        search_end = min(current_ayah_idx + self.context_window, len(quran_data))
        
        for target_idx in range(search_start, search_end):
            target_words = quran_data[target_idx]["words"]
            
            # Skip if target is empty
            if not target_words:
                continue
            
            # ============================================================
            # Step 3: Align and score
            # ============================================================
            alignment = self.alignment_engine.align(
                spoken_words=filtered_words,
                spoken_confidences=filtered_confidences,
                expected_words=target_words
            )
            
            # Count and score matches
            matches = [m for m in alignment if m.status == "correct"]
            match_count = len(matches)
            
            # Skip if too few matches
            if match_count < min_match_length:
                search_results.append({
                    "ayah_idx": target_idx,
                    "match_count": match_count,
                    "reason": "Too few matches"
                })
                continue
            
            # Compute average metrics
            avg_score = sum(m.score for m in matches) / len(matches) if matches else 0.0
            avg_conf = sum(m.confidence for m in matches) / len(matches) if matches else 0.0
            
            # Cost = 1 - avg_score (lower cost = better)
            cost = 1.0 - avg_score
            
            search_results.append({
                "ayah_idx": target_idx,
                "match_count": match_count,
                "average_score": round(avg_score, 3),
                "average_confidence": round(avg_conf, 3),
                "cost": round(cost, 3)
            })
            
            # Track best match
            if cost < best_cost:
                best_cost = cost
                best_result = {
                    "target_ayah": target_idx,
                    "match_length": match_count,
                    "average_score": avg_score,
                    "average_confidence": avg_conf,
                    "alignment": alignment,
                    "search_results": search_results
                }
        
        # ============================================================
        # Step 4: Validate best match against thresholds
        # ============================================================
        if not best_result:
            return JumpDetectionResult(
                detected=False,
                confidence_metrics={
                    "reason": "No candidates in context window",
                    "search_results": search_results
                }
            )
        
        # Check thresholds
        passes_match_length = best_result["match_length"] >= min_match_length
        passes_score = best_result["average_score"] >= min_average_score
        passes_confidence = best_result["average_confidence"] >= min_average_confidence
        
        detected = passes_match_length and passes_score and passes_confidence
        
        # Compute missed words
        missed_words = []
        if detected:
            for i in range(current_ayah_idx, best_result["target_ayah"]):
                missed_words.extend(quran_data[i]["words"])
        
        # ============================================================
        # Step 5: Return result
        # ============================================================
        return JumpDetectionResult(
            detected=detected,
            target_ayah=best_result["target_ayah"] if detected else -1,
            match_length=best_result["match_length"],
            average_score=round(best_result["average_score"], 3),
            average_confidence=round(best_result["average_confidence"], 3),
            alignment=best_result["alignment"],
            missed_word_count=len(missed_words),
            confidence_metrics={
                "passes_match_length": passes_match_length,
                "passes_score": passes_score,
                "passes_confidence": passes_confidence,
                "filtered_word_count": len(filtered_words),
                "total_word_count": len(spoken_words),
                "search_results": search_results
            }
        )
    
    def set_context_window(self, window_size: int):
        """Adjust lookahead distance for jump detection."""
        if window_size < 1:
            raise ValueError("context_window must be >= 1")
        self.context_window = window_size
    
    def adjust_thresholds(self, min_score: float = 0.80, min_confidence: float = 0.60):
        """
        Adjust detection thresholds (e.g., for stricter or looser detection).
        
        Args:
            min_score: Minimum average similarity score [0, 1]
            min_confidence: Minimum average confidence [0, 1]
        """
        self.min_score = min_score
        self.min_confidence = min_confidence


class MultiAyahMatcher:
    """
    Advanced matching across multiple ayahs (for context).
    
    Useful for:
    - Finding best matching ayah across entire surah
    - Handling large jumps (beyond context window)
    - Disambiguation when multiple matches possible
    """
    
    def __init__(self, alignment_engine: AlignmentEngine = None):
        """
        Args:
            alignment_engine: Reuse existing engine or create new
        """
        self.alignment_engine = alignment_engine or AlignmentEngine()
    
    def find_best_match_in_surah(
        self,
        spoken_words: List[str],
        spoken_confidences: List[ConfidenceData],
        quran_data: List[Dict],
        min_match_length: int = 2
    ) -> Optional[Dict]:
        """
        Find best matching ayah in entire surah (expensive, use sparingly).
        
        Returns:
            {
                "ayah_idx": int,
                "match_length": int,
                "average_score": float,
                "alignment": List[AlignmentMatch]
            }
            or None if no good match found
        """
        best_match = None
        best_score = 0.0
        
        for ayah_idx, ayah_data in enumerate(quran_data):
            ayah_words = ayah_data["words"]
            
            alignment = self.alignment_engine.align(
                spoken_words,
                spoken_confidences,
                ayah_words
            )
            
            match_count = sum(1 for m in alignment if m.status == "correct")
            if match_count < min_match_length:
                continue
            
            avg_score = sum(
                m.score for m in alignment if m.status == "correct"
            ) / match_count
            
            if avg_score > best_score:
                best_score = avg_score
                best_match = {
                    "ayah_idx": ayah_idx,
                    "match_length": match_count,
                    "average_score": avg_score,
                    "alignment": alignment
                }
        
        return best_match
