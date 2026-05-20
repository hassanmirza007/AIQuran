# app/tajweed/reference_builder.py
"""
Offline reference MFCC builder for Al-Fatiha.

Loads 7 local ayah MP3 files (Abdullah Basfar), concatenates them, runs Whisper
with deterministic settings to get word-level timestamps, slices the PCM per
word, extracts MFCC per slice, and returns an in-memory dict ready for DTW.

Audio loading: ffmpeg subprocess (no librosa/numba dependency).
MFCC:          python_speech_features (pure numpy, no numba dependency).

Called once at session startup — result is ~150KB in memory.
"""

import os
import subprocess
import unicodedata
import numpy as np
from python_speech_features import mfcc as compute_mfcc, delta as compute_delta
from app.utils.logger import get_logger

logger = get_logger(__name__)

SAMPLE_RATE = 16000

# Local reference audio: Abdullah Basfar, files named {surah}_{ayah}.mp3
REFERENCE_AUDIO_DIR = "data/reference"
SURAH_1_AYAH_COUNT  = 7

# Minimum word-slice length for reliable MFCC
MIN_SLICE_SAMPLES = int(SAMPLE_RATE * 0.05)  # 50ms

# MFCC parameters — keep consistent between reference and user audio
N_MFCC     = 13           # cepstral coefficients passed to compute_mfcc()
N_FEATURES = N_MFCC * 3   # 39: static + Δ + ΔΔ  (actual DTW feature dimension)
N_FILT     = 26
N_FFT      = 512
WIN_STEP   = 0.01         # 10ms hop

# DTW scoring constants — used with composite metric (0.5*mean + 0.5*p90).
# With 39D features (static+Δ+ΔΔ) and CMVN, Euclidean distances scale by ~√3
# compared to 13D.  Expected composite ranges:
#   correct recitation  → 4–6
#   minor harakah error → 9–14
#   significant error   → 18–26
DIST_PERFECT = 5.0    # composite → score 100
DIST_ZERO    = 26.0   # composite → score 0


def _apply_cmvn(mfcc_matrix: np.ndarray) -> np.ndarray:
    """
    Cepstral Mean Variance Normalisation.

    Subtracts the per-coefficient mean and divides by std across the utterance.
    This removes systematic channel/microphone differences between the studio
    reference recording and the user's microphone, so DTW distances reflect
    actual pronunciation similarity rather than recording conditions.

    Input/output shape: (T, F) — works for any feature dimension F
    """
    mean = np.mean(mfcc_matrix, axis=0)
    std  = np.std(mfcc_matrix,  axis=0) + 1e-8
    return ((mfcc_matrix - mean) / std).astype(np.float32)


def _dtw_to_score(normalized_distance: float) -> int:
    """
    Convert a path-normalized DTW distance to a 0–100 pronunciation score.

    Linear mapping between DIST_PERFECT (→100) and DIST_ZERO (→0).
    Adjust DIST_PERFECT and DIST_ZERO at the top of this file after observing
    real normalized distances from a few recitation runs.
    """
    ratio = (DIST_ZERO - normalized_distance) / (DIST_ZERO - DIST_PERFECT)
    return max(0, min(100, int(ratio * 100)))


def strip_diacritics(text: str) -> str:
    """
    Remove Arabic diacritics (harakat, shadda, sukun, tatweel) for dict key
    normalisation.  Lets 'بِسْمِ' and 'بسم' resolve to the same key.
    """
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(
        ch for ch in nfd
        if not (0x064B <= ord(ch) <= 0x065F) and ord(ch) != 0x0640
    )
    return unicodedata.normalize("NFC", stripped).strip()


def _load_mp3_ffmpeg(path: str) -> np.ndarray:
    """
    Decode an MP3 file to float32 PCM at 16kHz using ffmpeg.
    Faster Whisper already requires ffmpeg, so it's always available.
    """
    cmd = [
        "ffmpeg", "-nostdin", "-threads", "0",
        "-i", path,
        "-f", "s16le", "-ac", "1",
        "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE),
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, check=True)
    return np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0



def _extract_mfcc(audio_slice: np.ndarray) -> np.ndarray:
    """
    Extract 39D MFCC feature matrix from a float32 PCM slice.
    Returns shape (T, N_FEATURES=39) with CMVN applied — ready for DTW.

    Feature vector per frame: [13 static MFCC | 13 delta | 13 delta-delta]
    Delta features capture spectral velocity/acceleration, which distinguishes
    vowel transitions (/m/→/i/ vs /m/→/u/) even after CMVN normalisation.
    """
    if len(audio_slice) < MIN_SLICE_SAMPLES:
        return np.zeros((1, N_FEATURES), dtype=np.float32)

    peak = np.max(np.abs(audio_slice))
    if peak > 0:
        audio_slice = audio_slice / peak

    mfcc13 = compute_mfcc(
        audio_slice,
        samplerate=SAMPLE_RATE,
        numcep=N_MFCC,
        nfilt=N_FILT,
        nfft=N_FFT,
        winstep=WIN_STEP,
    )                                      # (T, 13)
    d1     = compute_delta(mfcc13, 2)     # (T, 13) — velocity
    d2     = compute_delta(d1,     2)     # (T, 13) — acceleration
    feat39 = np.hstack([mfcc13, d1, d2]) # (T, 39)
    return _apply_cmvn(feat39)            # (T, 39)


def build_reference_mfccs(
    whisper_engine,
    ayahs=None,
    audio_dir: str = REFERENCE_AUDIO_DIR,
    surah: int = 1,
) -> dict[str, list[np.ndarray]]:
    """
    Build the reference MFCC dict by processing each ayah file individually.

    Args:
        whisper_engine: WhisperEngine instance (model already loaded).
        ayahs:          List[Ayah] from quran_loader — used to build per-ayah
                        expected_context prompts.  Falls back to QURAN_PROMPT
                        if not provided.
        audio_dir:      Directory containing {surah}_{ayah}.mp3 files.
        surah:          Surah number (1 = Al-Fatiha).

    Returns:
        dict: stripped_word_text → list[mfcc_matrix (T, N_FEATURES=39)]
        List because the same word can appear multiple times (e.g. الرحيم).

    Each ayah is processed independently to avoid boundary errors that occur
    when all ayahs are concatenated into one long audio buffer.  whisper_engine
    .transcribe() is used directly so garbage-token filtering and low-confidence
    handling are applied — same pipeline as Stage 1 runtime.
    """
    ayah_count = len(ayahs) if ayahs else SURAH_1_AYAH_COUNT
    reference_mfccs: dict[str, list[np.ndarray]] = {}
    word_count = 0

    for ayah_idx in range(ayah_count):
        ayah_num = ayah_idx + 1
        filename = os.path.join(audio_dir, f"{surah}_{ayah_num}.mp3")

        if not os.path.exists(filename):
            logger.warning(f"Reference audio missing, skipping: {filename}")
            continue

        audio = _load_mp3_ffmpeg(filename)
        logger.debug(f"Ayah {ayah_num}: loaded {len(audio)/SAMPLE_RATE:.2f}s")

        # Use exact ayah text as the Whisper prompt so vocabulary is primed
        if ayahs and ayah_idx < len(ayahs):
            expected_context = " ".join(w.text for w in ayahs[ayah_idx].words)
        print(f"Expected context for Ayah {ayah_num}: '{expected_context}'")


        # Run our WhisperEngine — handles garbage tokens, low-confidence words,
        # repetition removal, word timestamps, all identical to Stage 1 runtime
        whisper_engine.transcribe(audio, expected_context=expected_context)
        word_confidences = whisper_engine.get_last_word_confidences()

        for w in word_confidences:
            # start/end added to word_confidences in Step 1 of Stage 2
            if w.get("start") is None or w.get("end") is None:
                continue

            raw_word = w["word"]
            if not raw_word:
                continue

            start_sample = int(w["start"] * SAMPLE_RATE)
            end_sample   = int(w["end"]   * SAMPLE_RATE)
            audio_slice  = audio[start_sample:end_sample]

            mfcc = _extract_mfcc(audio_slice)
            key  = strip_diacritics(raw_word)

            if key not in reference_mfccs:
                reference_mfccs[key] = []
            reference_mfccs[key].append(mfcc)

            word_count += 1
            logger.debug(
                f"  [{word_count}] Ayah {ayah_num} '{raw_word}' → '{key}' "
                f"({w['start']:.2f}s–{w['end']:.2f}s, conf={w['confidence']:.2f}, "
                f"mfcc={mfcc.shape})"
            )

    logger.info(
        f"Reference MFCC dict built: {len(reference_mfccs)} unique words, "
        f"{word_count} total entries across {ayah_count} ayahs"
    )
    return reference_mfccs


def lookup_reference_mfcc(
    reference_mfccs: dict[str, list[np.ndarray]],
    word: str,
) -> np.ndarray | None:
    """
    Return the reference MFCC for a word (stripped key lookup).
    If the word appears multiple times, returns the first occurrence.
    Returns None if the word is not in the reference dict.
    """
    key = strip_diacritics(word)
    entries = reference_mfccs.get(key)
    if not entries:
        return None
    return entries[0]
