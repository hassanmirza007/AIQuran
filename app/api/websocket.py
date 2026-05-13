# app/api/websocket.py

import json
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.stt.streaming import StreamingTranscriber
from app.data.schemas import WSEventType
from app.utils.logger import get_logger

logger = get_logger(__name__)
ws_router = APIRouter()


def _get_sessions() -> dict:
    from app.api.routes import _sessions
    return _sessions


def _emit(event: str, payload: dict) -> str:
    return json.dumps({"event": event, "payload": payload})


@ws_router.websocket("/ws/session/{session_id}")
async def websocket_recitation(websocket: WebSocket, session_id: str):
    """
    Real-time recitation WebSocket.

    Client → Server messages:
        {"type": "audio_chunk", "data": [float, ...]}   — float32 PCM at 16kHz
        {"type": "ping"}
        {"type": "close"}

    Server → Client events:
        listening  — ready for next word, includes expected word + position
        result     — validation results after phrase ends
        finished   — surah complete
        error      — server error
    """
    sessions = _get_sessions()
    if session_id not in sessions:
        await websocket.close(code=4004, reason="Session not found")
        return

    await websocket.accept()
    svc = sessions[session_id]
    sm = svc.state_manager
    streamer = StreamingTranscriber(svc.stt)
    
    # ✅ Track whether this is the first audio chunk (for auto-detection)
    first_chunk = True

    logger.info(f"WS connected: {session_id}")

    # Send initial listening event
    if not sm.is_finished():
        await websocket.send_text(_emit(WSEventType.LISTENING, {
            "word": sm.get_current_word(),
            "ayah": sm.get_current_ayah_number(),
            "word_index": sm.get_state().word_index,
            "progress": sm.progress_percent(),
        }))

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "ping":
                await websocket.send_text(_emit("pong", {}))
                continue

            if msg_type == "close":
                break

            if msg_type == "audio_chunk":
                chunk = msg.get("data", [])
                streamer.push_chunk(chunk)

                # Wait until phrase boundary detected
                if not streamer.is_ready():
                    continue

                expected_ctx = svc._get_expected_context()

                # Transcribe (non-blocking — runs in thread pool inside)
                transcript = await streamer.transcribe_async(expected_context=expected_ctx)
                streamer.reset()

                # Note: SequenceEngineV2 doesn't use per-word confidence adjustment
                # but this call is kept for compatibility with future versions
                # that might integrate Whisper confidence scores

                if not transcript.strip():
                    # Nothing heard — silently wait for next chunk (don't advance cursor)
                    # Don't send LISTENING event because it would advance UI state
                    logger.debug(f"Empty transcript - waiting for next audio chunk")
                    continue

                # ✅ Pass is_first_chunk flag for auto-detection
                results = svc.process_transcript(transcript, is_first_chunk=first_chunk)
                first_chunk = False  # Only first chunk gets auto-detection

                # Skip processing if no valid results (empty transcript)
                if not results:
                    continue

                payload = {
                    "transcript": transcript,
                    "results": results,
                    "state": {
                        "ayah_number": sm.get_current_ayah_number() if not sm.is_finished() else -1,
                        "word_index": sm.get_state().word_index,
                        "current_word": sm.get_current_word() if not sm.is_finished() else "",
                        "progress": sm.progress_percent(),
                        "is_finished": sm.is_finished(),
                    },
                    "stats": svc.get_stats(),
                }

                if sm.is_finished():
                    await websocket.send_text(_emit(WSEventType.FINISHED, payload))
                    break

                await websocket.send_text(_emit(WSEventType.RESULT, payload))

                # Tell client what word comes next
                await websocket.send_text(_emit(WSEventType.LISTENING, {
                    "word": sm.get_current_word(),
                    "ayah": sm.get_current_ayah_number(),
                    "word_index": sm.get_state().word_index,
                    "progress": sm.progress_percent(),
                }))

    except WebSocketDisconnect:
        logger.info(f"WS disconnected: {session_id}")
    except Exception as e:
        logger.error(f"WS error [{session_id}]: {e}", exc_info=True)
        try:
            await websocket.send_text(_emit(WSEventType.ERROR, {"message": str(e)}))
        except Exception:
            pass
    finally:
        logger.info(f"WS closed: {session_id}")
