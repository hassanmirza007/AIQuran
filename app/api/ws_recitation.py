# app/api/ws_recitation.py
"""
WebSocket endpoint for real-time Quran recitation.

Acts as a thin transport layer over ConcurrentRecitationSession — the same
pipeline used by --terminal-stream.  All phrase segmentation, silence
detection, transcription, and validation run inside ConcurrentRecitationSession
unchanged.  This module only handles WebSocket I/O and the async/sync bridge.

Client → Server:
    {"type": "audio_chunk", "data": [float32...]}  — float32 PCM at 16 kHz
    {"type": "ping"}
    {"type": "close"}

Server → Client:
    session_started   — full surah structure + initial pointer
    transcript        — raw Whisper text (before alignment)
    validation_result — per-word results with ayah/word_index + new pointer
    pointer_update    — current expected word position
    summary           — grouped mistakes + stats at session end
    error             — server-side error
"""

import json
import os
import queue
import threading

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.streaming.concurrent_session import ConcurrentRecitationSession
from app.services.recitation_service import RecitationService
from app.utils.config import config
from app.utils.logger import get_logger

logger = get_logger(__name__)
recitation_router = APIRouter()

# Per-surah JSON files live in data/surahs/ (surah_1.json … surah_114.json)
# plus index.json describing all 114 chapters for the frontend selector.
SURAH_DIR = "data/surahs"
SURAH_INDEX_FILE = os.path.join(SURAH_DIR, "index.json")


def _load_surah_index() -> list[dict]:
    with open(SURAH_INDEX_FILE, encoding="utf-8") as f:
        return json.load(f)


# Loaded once at import: list for the HTTP endpoint, dict for fast lookup/validation.
_SURAH_LIST = _load_surah_index()
_SURAH_BY_NUMBER = {s["surah"]: s for s in _SURAH_LIST}


def _resolve_surah(surah_param: str | None) -> tuple[int, str, str]:
    """
    Validate the requested surah number, falling back to surah 1.
    Returns (surah_number, surah_name, surah_file_path).
    """
    try:
        surah_number = int(surah_param) if surah_param is not None else 1
    except (TypeError, ValueError):
        surah_number = 1
    if surah_number not in _SURAH_BY_NUMBER:
        surah_number = 1
    surah_name = _SURAH_BY_NUMBER[surah_number]["name"]
    surah_file = os.path.join(SURAH_DIR, f"surah_{surah_number}.json")
    return surah_number, surah_name, surah_file


@recitation_router.get("/api/surahs")
async def list_surahs():
    """Surah catalogue for the frontend dropdown."""
    return _SURAH_LIST


# ---------------------------------------------------------------------------
# WebSocketAudioSource — phrase queue (frontend already did VAD + trimming)
# ---------------------------------------------------------------------------

class WebSocketAudioSource:
    """
    Simple phrase queue. The browser performs VAD and sends one complete,
    already-trimmed audio segment per phrase. push() enqueues it;
    get_phrase() blocks until one arrives or the source is closed.
    """

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._closed = False

    def push(self, samples: list[float]) -> None:
        """Called from async WebSocket handler. Thread-safe."""
        if not self._closed:
            self._q.put(np.array(samples, dtype=np.float32))

    def close(self) -> None:
        """Unblock any waiting get_phrase() call."""
        self._closed = True
        self._q.put(None)  # sentinel

    def get_phrase(self):
        """
        Block until a complete phrase arrives.
        Returns the float32 array, or None when the source is closed.
        """
        while True:
            try:
                phrase = self._q.get(timeout=1.0)
            except queue.Empty:
                if self._closed:
                    return None
                continue
            return phrase  # None sentinel passes through as the closed signal


# ---------------------------------------------------------------------------
# WebSocketRecorder — drop-in for SimpleRecorder; no VAD needed on backend
# ---------------------------------------------------------------------------

class WebSocketRecorder:
    """
    Drop-in replacement for the terminal SimpleRecorder.

    record_until_pause() simply waits for one complete phrase from the browser
    — VAD and trimming are already done on the frontend with the same
    constants as recorder.py, so the backend pipeline is identical.
    """

    def __init__(self, audio_source: WebSocketAudioSource):
        self._source = audio_source

    def record_until_pause(self):
        """Block until a complete phrase arrives. Returns None when closed."""
        return self._source.get_phrase()


# ---------------------------------------------------------------------------
# Position annotation — attaches (ayah, word_index) to every result entry
# ---------------------------------------------------------------------------

def _annotate_positions(
    results: list[dict],
    pre_ayah: int,
    pre_word_index: int,
    ayah_word_counts: dict,
) -> list[dict]:
    """
    Walk from (pre_ayah, pre_word_index) and attach explicit position fields
    to each result so the frontend never has to infer position locally.
    """
    ayah = pre_ayah
    word_idx = pre_word_index

    def advance():
        nonlocal ayah, word_idx
        count = ayah_word_counts.get(ayah, 0)
        if word_idx + 1 < count:
            word_idx += 1
        else:
            next_ayah = ayah + 1
            if next_ayah in ayah_word_counts:
                ayah = next_ayah
                word_idx = 0

    augmented = []
    for r in results:
        r = dict(r)
        status = r.get("status", "")
        if status in ("correct", "incorrect", "missed", "partial_match"):
            r["ayah"] = ayah
            r["word_index"] = word_idx
            advance()
        elif status == "jump":
            missed_words = r.get("missed_words", [])
            missed_positions = []
            for word in missed_words:
                missed_positions.append({"ayah": ayah, "word_index": word_idx, "word": word})
                advance()
            r["missed_positions"] = missed_positions
            r["ayah"] = None
            r["word_index"] = None
        else:
            r["ayah"] = None
            r["word_index"] = None
        augmented.append(r)
    return augmented


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@recitation_router.websocket("/ws/recitation")
async def ws_recitation(websocket: WebSocket):
    await websocket.accept()

    # Frontend selects the surah via ?surah=<n>; defaults to 1 if absent/invalid.
    surah_number, surah_name, surah_file = _resolve_surah(websocket.query_params.get("surah"))
    logger.info(f"WS /recitation connected — surah {surah_number} ({surah_name})")

    try:
        svc = RecitationService(
            surah_file=surah_file,
            surah_number=surah_number,
            model_size=config.WHISPER_MODEL_SIZE,
        )
    except Exception as e:
        await websocket.send_text(json.dumps({"type": "error", "message": f"Failed to load surah: {e}"}))
        await websocket.close()
        return

    svc.is_locked = True
    sm = svc.state_manager
    ayah_word_counts = {a.ayah_number: len(a.words) for a in svc.ayahs}

    # Bridge: async WebSocket → sync _capture_loop
    audio_source = WebSocketAudioSource()

    # Sync→async event bridge: validation thread puts here, WebSocket loop reads
    event_q: queue.Queue = queue.Queue()

    def event_cb(event_type: str, payload: dict) -> None:
        """
        Called from ConcurrentRecitationSession background threads.
        Enriches validation_result with per-word positions, then queues the
        JSON message for the async WebSocket sender.
        """
        payload = dict(payload)
        if event_type == "validation_result":
            pre_ayah = payload.pop("_pre_ayah", None)
            pre_word = payload.pop("_pre_word", None)
            if pre_ayah is not None:
                payload["results"] = _annotate_positions(
                    payload["results"], pre_ayah, pre_word, ayah_word_counts
                )
        elif event_type == "summary":
            # _session_results use 0-based _ayah_index + status; frontend expects ayah_number + type
            transformed = []
            for m in payload.get("mistakes", []):
                idx = m.get("_ayah_index", 0)
                ayah_num = svc.ayahs[idx].ayah_number if idx < len(svc.ayahs) else idx + 1
                transformed.append({
                    "ayah":     ayah_num,
                    "type":     m.get("status", ""),
                    "expected": m.get("expected", ""),
                    "spoken":   m.get("spoken", ""),
                })
            payload["mistakes"] = transformed
        event_q.put(json.dumps({"type": event_type, **payload}))

    ws_recorder = WebSocketRecorder(audio_source)
    session = ConcurrentRecitationSession(service=svc, recorder=ws_recorder)
    session.set_ws_mode(event_cb)

    # Send session_started before the session thread starts producing events
    await websocket.send_text(json.dumps({
        "type":        "session_started",
        "surah_number": surah_number,
        "surah_name":  surah_name,
        "ayahs": [
            {"ayah_number": a.ayah_number, "words": [w.text for w in a.words]}
            for a in svc.ayahs
        ],
        "current_pointer": {
            "ayah":       sm.get_current_ayah_number(),
            "word_index": sm.get_state().word_index,
        },
    }))

    # Run ConcurrentRecitationSession.start() in a daemon thread
    session_thread = threading.Thread(target=session.start, daemon=True, name="WS-Session")
    session_thread.start()

    # Concurrent receive (audio in) + send (events out)
    import asyncio
    recv_task  = asyncio.create_task(websocket.receive_text())
    finished   = False

    try:
        while not finished:
            # Drain any queued events first (non-blocking)
            while not event_q.empty():
                msg = event_q.get_nowait()
                await websocket.send_text(msg)
                if '"type": "summary"' in msg:
                    finished = True
                    break
            if finished:
                break

            # Wait for next incoming message (with short timeout to recheck events)
            try:
                raw = await asyncio.wait_for(recv_task, timeout=0.05)
            except asyncio.TimeoutError:
                recv_task = asyncio.create_task(websocket.receive_text())
                continue

            msg = json.loads(raw)
            t = msg.get("type")
            if t == "audio_chunk":
                audio_source.push(msg.get("data", []))
            elif t == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            elif t == "stop":
                # User stopped early — emit partial summary of validated results so far
                event_cb("summary", {
                    "mistakes": list(session._session_results),
                    "stats":    svc.get_stats(),
                })
                finished = True
                break
            elif t == "close":
                finished = True
                break

            recv_task = asyncio.create_task(websocket.receive_text())

        # Drain any remaining events after the loop exits
        while not event_q.empty():
            await websocket.send_text(event_q.get_nowait())

    except WebSocketDisconnect:
        logger.info("WS /recitation disconnected")
    except Exception as e:
        logger.error(f"WS /recitation error: {e}", exc_info=True)
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass
    finally:
        recv_task.cancel()
        audio_source.close()
        session.stop_event.set()
        logger.info("WS /recitation closed")
