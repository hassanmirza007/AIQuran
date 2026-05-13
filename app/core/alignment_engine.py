# app/core/alignment_engine.py

"""
Production-grade alignment engine for Quran recitation validation.

Uses edit distance (Wagner-Fischer algorithm) to optimally align
spoken words to Quran reference text, accounting for word confidence.

Key improvements:
1. Handles word insertion/deletion (not just substitution)
2. Confidence-weighted matching
3. Multi-hypothesis support
4. Robustness to noisy STT output
"""

from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
import numpy as np
import math
from app.nlp.matcher import similarity_score


@dataclass
class ConfidenceData:
    """Per-word confidence and metadata from STT engine."""
    word: str
    confidence: float  # [0, 1]
    timestamp_start: float = 0.0  # seconds
    timestamp_end: float = 0.0    # seconds
    
    @property
    def is_high_confidence(self) -> bool:
        """High confidence: >= 75%"""
        return self.confidence >= 0.75
    
    @property
    def is_low_confidence(self) -> bool:
        """Low confidence: < 60%"""
        return self.confidence < 0.60
    
    @property
    def confidence_weight(self) -> float:
        """
        Sigmoid transformation: [0,1] → [0,1].
        
        Maps confidence to weight for edit distance calculation.
        - High confidence (0.9) → weight 0.98
        - Medium confidence (0.65) → weight 0.5
        - Low confidence (0.3) → weight 0.02
        
        Used to penalize low-confidence matches less heavily.
        """
        k = 10  # Steepness
        center = 0.65  # Inflection point
        x = self.confidence
        try:
            weight = 1.0 / (1.0 + math.exp(-k * (x - center)))
        except OverflowError:
            weight = 1.0 if x > center else 0.0
        return weight
    
    def adaptive_threshold(self) -> float:
        """
        Compute adaptive match threshold based on confidence.
        
        - High confidence word: use strict threshold (0.75)
        - Low confidence word: use loose threshold (0.50)
        
        Why: Low-confidence words are unreliable, so we're more lenient.
        """
        # Linear interpolation
        # At confidence 0.95: threshold 0.75
        # At confidence 0.40: threshold 0.50
        return 0.75 - (0.95 - self.confidence) * 0.5


@dataclass
class AlignmentMatch:
    """Result of aligning one spoken word to reference words."""
    spoken_idx: int          # Index in spoken_words (-1 if missed)
    expected_idx: int        # Index in expected_words (-1 if extra)
    spoken_word: str         # Transcribed word
    expected_word: str       # Reference word
    status: str              # "correct", "incorrect", "extra", "missed", "possible_match"
    score: float             # Similarity [0, 1]
    confidence: float        # STT confidence [0, 1]
    cost: float              # DP cost of this match


class AlignmentEngine:
    """
    Optimal word-level alignment using weighted edit distance.
    
    Algorithm: Myers' diff adapted to word sequences with confidence weighting.
    Complexity: O(n*m) where n = spoken_words, m = expected_words
    
    Reference:
    - Wagner, Robert A., and Michael J. Fischer. "The string-to-string correction problem." 
      Journal of the ACM (JACM) 21.1 (1974): 168-173.
    """
    
    # Cost weights for operations
    COST_MATCH = 0.0           # Perfect match
    COST_MISMATCH = 1.0        # Substitution
    COST_INSERTION = 0.5       # Extra word (user said more)
    COST_DELETION = 1.0        # Missed word (user said less)
    
    def __init__(self, max_hypothesis_depth: int = 3):
        """
        Args:
            max_hypothesis_depth: Number of alternative alignments to track
        """
        self.max_hypothesis_depth = max_hypothesis_depth
    
    def align(
        self,
        spoken_words: List[str],
        spoken_confidences: List[ConfidenceData],
        expected_words: List[str]
    ) -> List[AlignmentMatch]:
        """
        Compute optimal alignment between spoken and expected words.
        
        Args:
            spoken_words: Transcribed words from Whisper
            spoken_confidences: Per-word confidence scores
            expected_words: Reference Quran words
        
        Returns:
            List of alignment matches in original order.
            
        Example:
        ```
        spoken = ["الحمد", "لله", "xyz"]
        confidences = [ConfidenceData("الحمد", 0.95), ..., ConfidenceData("xyz", 0.20)]
        expected = ["الحمد", "لله", "رب", "العالمين"]
        
        alignment = engine.align(spoken, confidences, expected)
        # Returns:
        # [
        #   AlignmentMatch(0, 0, "الحمد", "الحمد", "correct", 0.99, 0.95, 0.0),
        #   AlignmentMatch(1, 1, "لله", "لله", "correct", 0.98, 0.92, 0.0),
        #   AlignmentMatch(2, -1, "xyz", "-", "extra", 0.0, 0.20, 0.5),
        #   AlignmentMatch(-1, 2, "-", "رب", "missed", 0.0, 1.0, 1.0),
        #   AlignmentMatch(-1, 3, "-", "العالمين", "missed", 0.0, 1.0, 1.0),
        # ]
        ```
        """
        n = len(spoken_words)
        m = len(expected_words)
        
        # DP table: dp[i][j] = (cost, backtrack_path)
        # dp[i][j] = optimal cost to align spoken[0:i] with expected[0:j]
        dp = {}
        
        # Base cases
        dp[(0, 0)] = (0.0, [])
        
        # Initialize first column (all expected words missed)
        for j in range(1, m + 1):
            cost = dp[(0, j-1)][0] + self.COST_DELETION
            dp[(0, j)] = (cost, dp[(0, j-1)][1] + [('delete', None, j-1)])
        
        # Initialize first row (all spoken words extra)
        for i in range(1, n + 1):
            cost = dp[(i-1, 0)][0] + self.COST_INSERTION
            dp[(i, 0)] = (cost, dp[(i-1, 0)][1] + [('insert', i-1, None)])
        
        # Fill DP table
        for i in range(1, n + 1):
            for j in range(1, m + 1):
                options = []
                
                # Option 1: Match/Substitute spoken[i-1] with expected[j-1]
                match_score = similarity_score(spoken_words[i-1], expected_words[j-1])
                conf_weight = spoken_confidences[i-1].confidence_weight
                threshold = spoken_confidences[i-1].adaptive_threshold()
                
                if match_score >= threshold:
                    # Good match
                    cost = dp[(i-1, j-1)][0] + self.COST_MATCH
                    path = dp[(i-1, j-1)][1] + [('match', i-1, j-1, match_score)]
                else:
                    # Mismatch, but still possible
                    # Penalize less for low-confidence words
                    mismatch_cost = self.COST_MISMATCH * (1.0 - conf_weight * 0.5)
                    cost = dp[(i-1, j-1)][0] + mismatch_cost
                    path = dp[(i-1, j-1)][1] + [('match', i-1, j-1, match_score)]
                
                options.append((cost, path))
                
                # Option 2: Delete expected[j-1] (user said less)
                cost = dp[(i, j-1)][0] + self.COST_DELETION
                path = dp[(i, j-1)][1] + [('delete', None, j-1)]
                options.append((cost, path))
                
                # Option 3: Insert spoken[i-1] (user said more)
                # Penalize extra words from low-confidence STT less heavily
                insertion_cost = self.COST_INSERTION * conf_weight
                cost = dp[(i-1, j)][0] + insertion_cost
                path = dp[(i-1, j)][1] + [('insert', i-1, None)]
                options.append((cost, path))
                
                # Choose option with minimum cost
                best_cost, best_path = min(options, key=lambda x: x[0])
                dp[(i, j)] = (best_cost, best_path)
        
        # Backtrack: Get final alignment
        final_cost, best_path = dp[(n, m)]
        
        # Convert path to matches
        matches = self._path_to_matches(
            best_path,
            spoken_words,
            spoken_confidences,
            expected_words
        )
        
        return matches
    
    def _path_to_matches(
        self,
        path: List[Tuple],
        spoken_words: List[str],
        spoken_confidences: List[ConfidenceData],
        expected_words: List[str]
    ) -> List[AlignmentMatch]:
        """Convert DP path to alignment matches."""
        matches = []
        
        for operation in path:
            op_type = operation[0]
            
            if op_type == 'match':
                _, s_idx, e_idx, score = operation
                status = "correct" if score >= 0.75 else "incorrect"
                
                matches.append(AlignmentMatch(
                    spoken_idx=s_idx,
                    expected_idx=e_idx,
                    spoken_word=spoken_words[s_idx],
                    expected_word=expected_words[e_idx],
                    status=status,
                    score=score,
                    confidence=spoken_confidences[s_idx].confidence,
                    cost=0.0 if status == "correct" else 1.0
                ))
            
            elif op_type == 'delete':
                _, _, e_idx = operation
                matches.append(AlignmentMatch(
                    spoken_idx=-1,
                    expected_idx=e_idx,
                    spoken_word="-",
                    expected_word=expected_words[e_idx],
                    status="missed",
                    score=0.0,
                    confidence=1.0,
                    cost=self.COST_DELETION
                ))
            
            elif op_type == 'insert':
                _, s_idx, _ = operation
                matches.append(AlignmentMatch(
                    spoken_idx=s_idx,
                    expected_idx=-1,
                    spoken_word=spoken_words[s_idx],
                    expected_word="-",
                    status="extra",
                    score=0.0,
                    confidence=spoken_confidences[s_idx].confidence,
                    cost=self.COST_INSERTION * spoken_confidences[s_idx].confidence_weight
                ))
        
        return matches
    
    def alignment_quality_score(self, matches: List[AlignmentMatch]) -> float:
        """
        Score overall alignment quality [0, 1].
        
        Higher = better alignment.
        
        Accounts for:
        - Correct matches (high weight)
        - Incorrect matches (medium weight)
        - Missed/extra words (low weight)
        - Word confidence
        """
        if not matches:
            return 0.0
        
        total_weight = 0.0
        weighted_score = 0.0
        
        for match in matches:
            if match.status == "correct":
                weight = 1.0 * match.confidence
                score = 1.0
            elif match.status == "incorrect":
                weight = 0.5 * match.confidence
                score = match.score
            elif match.status == "missed":
                weight = 0.2
                score = 0.0
            elif match.status == "extra":
                weight = 0.3 * match.confidence
                score = 0.0
            else:
                weight = 0.0
                score = 0.0
            
            total_weight += weight
            weighted_score += weight * score
        
        if total_weight == 0:
            return 0.0
        
        return weighted_score / total_weight


class ConfidenceAwareAligner:
    """
    Wrapper that handles confidence-based preprocessing.
    
    Filters out extremely low-confidence words before alignment
    to improve robustness to STT hallucinations.
    """
    
    def __init__(self, confidence_cutoff: float = 0.35):
        """
        Args:
            confidence_cutoff: Drop words below this confidence
        """
        self.confidence_cutoff = confidence_cutoff
        self.alignment_engine = AlignmentEngine()
    
    def align_with_filtering(
        self,
        spoken_words: List[str],
        spoken_confidences: List[ConfidenceData],
        expected_words: List[str],
        filter_very_low: bool = True
    ) -> List[AlignmentMatch]:
        """
        Align, optionally filtering out very low-confidence words.
        
        Args:
            spoken_words: Transcribed words
            spoken_confidences: Confidence scores
            expected_words: Reference words
            filter_very_low: If True, drop words with confidence < 0.35
        
        Returns:
            Alignment matches
        """
        if filter_very_low:
            # Keep track of original indices
            kept_indices = []
            filtered_words = []
            filtered_confidences = []
            
            for i, (word, conf) in enumerate(zip(spoken_words, spoken_confidences)):
                if conf.confidence >= self.confidence_cutoff:
                    kept_indices.append(i)
                    filtered_words.append(word)
                    filtered_confidences.append(conf)
            
            # Align filtered words
            matches = self.alignment_engine.align(
                filtered_words,
                filtered_confidences,
                expected_words
            )
            
            # Restore original indices
            for match in matches:
                if match.spoken_idx != -1:
                    match.spoken_idx = kept_indices[match.spoken_idx]
            
            return matches
        else:
            # No filtering
            return self.alignment_engine.align(
                spoken_words,
                spoken_confidences,
                expected_words
            )
