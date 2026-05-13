# app/stt/streaming.py

import asyncio
import numpy as np
from app.utils.logger import get_logger

logger = get_logger(__name__)

SAMPLE_RATE = 16000
# Minimum audio length before attempting transcription (avoids partial-word artifacts)
MIN_CHUNK_SECONDS = 1.0  # Reduced from 1.5s for faster response
MIN_SAMPLES = int(SAMPLE_RATE * MIN_CHUNK_SECONDS)

# Silence detection for stream-based recording (more sensitive for speech detection)
SILENCE_RMS_THRESHOLD = 0.004  # Reduced from 0.008 for better speech detection
SILENCE_FRAMES_REQUIRED = int(SAMPLE_RATE * 0.6)   # 0.6s of silence = end of phrase (reduced from 0.8s)


class AudioStreamBuffer:
    """
    Accumulates raw float32 PCM chunks from a WebSocket stream and decides
    when enough audio has been collected to attempt transcription.

    Usage in WebSocket handler:
        buffer = AudioStreamBuffer()
        while receiving:
            chunk = await ws.receive_json()["data"]
            buffer.push(chunk)
            if buffer.ready():
                audio = buffer.flush()
                transcript = stt.transcribe(audio)
                buffer.reset()
    """

    def __init__(self):
        self._samples: list[float] = []
        self._silent_samples: int = 0
        self._speech_started: bool = False

    def push(self, chunk: list[float]):
        """Add a new audio chunk (list of float32 samples)."""
        self._samples.extend(chunk)

        # Convert to numpy for analysis
        chunk_np = np.array(chunk, dtype=np.float32)
        
        # Calculate RMS (Root Mean Square) for silence detection
        rms = float(np.sqrt(np.mean(chunk_np ** 2))) if len(chunk_np) > 0 else 0.0
        
        # Add some logging for debugging
        logger.debug(f"Audio chunk RMS: {rms:.6f}, samples so far: {len(self._samples)}, speech_started: {self._speech_started}")

        if rms > SILENCE_RMS_THRESHOLD:
            self._speech_started = True
            self._silent_samples = 0
        elif self._speech_started:
            self._silent_samples += len(chunk)

    def ready(self) -> bool:
        """
        Return True when enough audio has been buffered and speech has ended.
        Criteria:
          1. At least MIN_SAMPLES of audio collected.
          2. Speech was detected (non-silent chunk received).
          3. Followed by SILENCE_FRAMES_REQUIRED samples of silence (phrase ended).
        """
        if len(self._samples) < MIN_SAMPLES:
            return False
        if not self._speech_started:
            return False
        return self._silent_samples >= SILENCE_FRAMES_REQUIRED

    def flush(self) -> np.ndarray:
        """Return the accumulated audio as a float32 numpy array."""
        return np.array(self._samples, dtype=np.float32)

    def reset(self):
        """Clear the buffer after a transcription has been dispatched."""
        self._samples = []
        self._silent_samples = 0
        self._speech_started = False

    def sample_count(self) -> int:
        return len(self._samples)

    def duration_seconds(self) -> float:
        return len(self._samples) / SAMPLE_RATE


class StreamingTranscriber:
    """
    Wraps AudioStreamBuffer + WhisperEngine for use inside the WebSocket handler.

    Runs Whisper in a thread pool executor so it never blocks the async event loop.
    """

    def __init__(self, stt_engine):
        self.stt = stt_engine
        self.buffer = AudioStreamBuffer()

    def push_chunk(self, chunk: list[float]):
        self.buffer.push(chunk)

    def is_ready(self) -> bool:
        return self.buffer.ready()

    async def transcribe_async(self, expected_context: str = "") -> str:
        """
        Flush the buffer and run Whisper in a thread pool.
        Returns transcript string. Resets the buffer automatically.
        """
        audio = self.buffer.flush()
        self.buffer.reset()

        if len(audio) == 0:
            return ""

        loop = asyncio.get_event_loop()
        transcript = await loop.run_in_executor(
            None,
            lambda: self.stt.transcribe(audio, expected_context=expected_context)
        )

        logger.debug(
            f"StreamingTranscriber: {len(audio)/SAMPLE_RATE:.1f}s audio "
            f"→ '{transcript}'"
        )
        return transcript

    def reset(self):
        self.buffer.reset()
