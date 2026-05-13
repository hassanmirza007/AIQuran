# app/utils/config.py

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Whisper
    WHISPER_MODEL_SIZE: str = os.getenv("WHISPER_MODEL_SIZE", "small")

    # Audio
    SAMPLE_RATE: int = int(os.getenv("SAMPLE_RATE", "16000"))
    MAX_RECORD_SECONDS: float = float(os.getenv("MAX_RECORD_SECONDS", "10.0"))
    SILENCE_THRESHOLD: float = float(os.getenv("SILENCE_THRESHOLD", "0.01"))
    SILENCE_DURATION: float = float(os.getenv("SILENCE_DURATION", "1.2"))

    # Matching
    MATCH_THRESHOLD: float = float(os.getenv("MATCH_THRESHOLD", "0.82"))
    LOOKAHEAD_WORDS: int = int(os.getenv("LOOKAHEAD_WORDS", "4"))

    # Data
    DEFAULT_SURAH_FILE: str = os.getenv("DEFAULT_SURAH_FILE", "data/al_fatiha.json")

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"


config = Config()
