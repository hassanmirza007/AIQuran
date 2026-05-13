# app/data/schemas.py

from pydantic import BaseModel, Field
from typing import Optional, List


# ------------------------------------------------------------------ #
#  Inbound — client → server
# ------------------------------------------------------------------ #

class StartSessionRequest(BaseModel):
    surah_number: int = Field(..., ge=1, le=114, description="Surah number 1-114")
    start_ayah: Optional[int] = Field(None, ge=1, description="Optional: start from this ayah")
    start_word: Optional[int] = Field(None, ge=0, description="Optional: start from this word index")


class TextInputRequest(BaseModel):
    session_id: str
    text: str = Field(..., min_length=1)


# ------------------------------------------------------------------ #
#  Outbound — server → client
# ------------------------------------------------------------------ #

class WordResult(BaseModel):
    spoken: str
    expected: str
    status: str           # "correct" | "incorrect" | "missed" | "empty"
    error_type: Optional[str]
    score: float
    confidence: str       # "high" | "medium" | "low"
    message: str
    missed_words: List[str] = []


class SessionState(BaseModel):
    session_id: str
    surah_number: int
    ayah_number: int
    word_index: int
    current_word: str
    progress_percent: float
    is_finished: bool


class SessionStats(BaseModel):
    total: int
    correct: int
    missed: int
    incorrect: int
    accuracy: float


class RecitationResponse(BaseModel):
    session_id: str
    results: List[WordResult]
    state: SessionState
    stats: SessionStats


# ------------------------------------------------------------------ #
#  WebSocket message envelopes
# ------------------------------------------------------------------ #

class WSEventType:
    RESULT = "result"          # validation result after each audio chunk
    STATE = "state"            # current cursor position update
    ERROR = "error"            # server-side error
    FINISHED = "finished"      # surah completed
    LISTENING = "listening"    # server is ready for audio


class WSMessage(BaseModel):
    event: str
    payload: dict
