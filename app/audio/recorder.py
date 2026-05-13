# app/audio/recorder.py

import sounddevice as sd
import numpy as np
from scipy.io import wavfile
from datetime import datetime
import os

SAMPLE_RATE = 16000
CHUNK_SIZE = 1024          # frames per chunk
SILENCE_THRESHOLD = 0.01   # RMS energy below this = silence
SILENCE_DURATION = 1.2     # seconds of silence to auto-stop recording
MAX_DURATION = 10.0        # hard cap — never record more than this


def _rms(chunk: np.ndarray) -> float:
    """Root mean square energy of an audio chunk."""
    return float(np.sqrt(np.mean(chunk ** 2)))


def record_audio(
    max_duration: float = MAX_DURATION,
    silence_threshold: float = SILENCE_THRESHOLD,
    silence_duration: float = SILENCE_DURATION,
) -> np.ndarray:
    """
    Record audio from the microphone, stopping automatically when the user
    stops speaking (silence-triggered) or when max_duration is reached.

    Returns a float32 numpy array at 16kHz suitable for Whisper input.

    Why this matters:
        The old fixed-4-second recorder sent 2-3s of silence to Whisper
        after every short word, causing transcription errors and hallucinations.
        This version detects when speech ends and cuts immediately.
    """
    print("🎤 Speak now (stops automatically when you pause)...")

    frames = []
    silent_chunks = 0
    speaking_started = False

    # How many silent chunks = silence_duration seconds of silence
    chunks_per_second = SAMPLE_RATE / CHUNK_SIZE
    required_silent_chunks = int(silence_duration * chunks_per_second)
    max_chunks = int(max_duration * chunks_per_second)

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32', blocksize=CHUNK_SIZE) as stream:
        for _ in range(max_chunks):
            chunk, _ = stream.read(CHUNK_SIZE)
            chunk = chunk.flatten()
            energy = _rms(chunk)

            frames.append(chunk)

            if energy > silence_threshold:
                speaking_started = True
                silent_chunks = 0
            elif speaking_started:
                silent_chunks += 1
                if silent_chunks >= required_silent_chunks:
                    break

    print("✅ Recording complete")

    audio = np.concatenate(frames, axis=0)

    # Trim leading silence before returning
    audio = _trim_silence(audio, silence_threshold)

    # Save audio as WAV file
    os.makedirs("debug_audio", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    wav_path = f"debug_audio/recording_{timestamp}.wav"
    wavfile.write(wav_path, SAMPLE_RATE, (audio * 32767).astype(np.int16))
    print(f"💾 Saved to {wav_path}")

    return audio


def _trim_silence(
    audio: np.ndarray,
    threshold: float = SILENCE_THRESHOLD,
    frame_size: int = CHUNK_SIZE,
) -> np.ndarray:
    """
    Trim leading and trailing silence from a recording.
    Operates frame-by-frame to preserve speech onset accurately.
    """
    total_frames = len(audio) // frame_size
    start_frame = 0
    end_frame = total_frames

    # Find first non-silent frame
    for i in range(total_frames):
        chunk = audio[i * frame_size:(i + 1) * frame_size]
        if _rms(chunk) > threshold:
            start_frame = max(0, i - 1)  # keep one frame of lead-in
            break

    # Find last non-silent frame
    for i in range(total_frames - 1, -1, -1):
        chunk = audio[i * frame_size:(i + 1) * frame_size]
        if _rms(chunk) > threshold:
            end_frame = min(total_frames, i + 2)  # keep one frame of tail
            break

    return audio[start_frame * frame_size:end_frame * frame_size]
