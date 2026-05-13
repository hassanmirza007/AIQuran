# app/api/routes.py

import uuid
from fastapi import APIRouter, HTTPException
from app.data.schemas import (
    StartSessionRequest, TextInputRequest,
    RecitationResponse, SessionState, SessionStats, WordResult,
)
from app.services.recitation_service import RecitationService
from app.services.start_detector import StartDetector
from app.utils.config import config
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["recitation"])

# In-memory session store. Replace with Redis for production multi-instance.
_sessions: dict[str, RecitationService] = {}


def _get_session(session_id: str) -> RecitationService:
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return _sessions[session_id]


def _build_state(session_id: str, svc: RecitationService) -> SessionState:
    sm = svc.state_manager
    finished = sm.is_finished()
    return SessionState(
        session_id=session_id,
        surah_number=sm.get_state().surah,
        ayah_number=sm.get_current_ayah_number() if not finished else -1,
        word_index=sm.get_state().word_index,
        current_word=sm.get_current_word() if not finished else "",
        progress_percent=sm.progress_percent(),
        is_finished=finished,
    )


def _build_stats(svc: RecitationService) -> SessionStats:
    return SessionStats(**svc.get_stats())


@router.post("/sessions", summary="Start a new recitation session")
def start_session(body: StartSessionRequest) -> dict:
    session_id = str(uuid.uuid4())
    surah_file = f"data/surah_{body.surah_number}.json"

    try:
        svc = RecitationService(
            surah_file=surah_file,
            surah_number=body.surah_number,
            model_size=config.WHISPER_MODEL_SIZE,
        )
    except FileNotFoundError:
        logger.info(f"Surah {body.surah_number} not cached — fetching from Quran.com API")
        try:
            from app.data.quran_loader import save_surah_to_json
            save_surah_to_json(body.surah_number, surah_file)
            svc = RecitationService(
                surah_file=surah_file,
                surah_number=body.surah_number,
                model_size=config.WHISPER_MODEL_SIZE,
            )
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Could not load surah: {e}")

    if body.start_ayah is not None:
        try:
            svc.state_manager.seek(
                ayah_index=body.start_ayah - 1,
                word_index=body.start_word or 0,
            )
            # ✅ Mark as locked when explicit start_ayah is provided
            svc.is_locked = True
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    _sessions[session_id] = svc
    logger.info(f"Session started: {session_id} | Surah {body.surah_number}")

    return {"session_id": session_id, "state": _build_state(session_id, svc)}


@router.post("/sessions/{session_id}/text", summary="Submit transcript for validation")
def submit_text(session_id: str, body: TextInputRequest) -> RecitationResponse:
    svc = _get_session(session_id)
    if svc.state_manager.is_finished():
        raise HTTPException(status_code=400, detail="Session already finished")

    raw = svc.process_transcript(body.text)
    return RecitationResponse(
        session_id=session_id,
        results=[WordResult(**r) for r in raw],
        state=_build_state(session_id, svc),
        stats=_build_stats(svc),
    )


@router.post("/sessions/{session_id}/detect-start", summary="Detect and lock starting position from transcript")
def detect_start(session_id: str, body: TextInputRequest) -> dict:
    """
    Automatically detect where the user started reciting and lock to that position.
    
    Use this endpoint when you want auto-detection instead of manual start_ayah specification.
    
    NOTE: This endpoint can only be called ONCE per session. After locking, re-detection is not allowed.
    """
    svc = _get_session(session_id)
    
    # ✅ Check if already locked - prevent re-locking
    if svc.is_locked:
        raise HTTPException(
            status_code=400,
            detail="Session already locked to starting position. Cannot re-detect. Create a new session if you need to start over."
        )
    
    # Create detector for the loaded ayahs
    detector = StartDetector(svc.ayahs, threshold=0.78, min_words=2)
    
    # Detect starting position
    detection = detector.detect_start(body.text)
    
    if not detection:
        raise HTTPException(
            status_code=400,
            detail="Could not detect starting position with sufficient confidence. Please try again or specify start_ayah manually."
        )
    
    # Lock to detected position
    try:
        svc.state_manager.seek(
            ayah_index=detection["ayah_index"],
            word_index=detection["word_index"]
        )
        # ✅ Mark as locked after successful detection
        svc.is_locked = True
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    logger.info(
        f"Auto-detected start: Session {session_id} → "
        f"Ayah {detection['ayah_number']}, Word {detection['word_index']}, "
        f"Confidence {detection['confidence']}"
    )
    
    return {
        "detected": True,
        "ayah_number": detection["ayah_number"],
        "ayah_index": detection["ayah_index"],
        "word_index": detection["word_index"],
        "confidence": detection["confidence"],
        "state": _build_state(session_id, svc),
    }


@router.get("/sessions/{session_id}/state", summary="Get current session state")
def get_state(session_id: str) -> SessionState:
    return _build_state(session_id, _get_session(session_id))


@router.get("/sessions/{session_id}/stats", summary="Get session accuracy stats")
def get_stats(session_id: str) -> SessionStats:
    return _build_stats(_get_session(session_id))


@router.delete("/sessions/{session_id}", summary="End a session")
def end_session(session_id: str) -> dict:
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    del _sessions[session_id]
    logger.info(f"Session ended: {session_id}")
    return {"message": "Session ended"}


@router.get("/health")
def health():
    return {"status": "ok", "active_sessions": len(_sessions)}
