# app/main.py

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.api.websocket import ws_router
from app.api.ws_recitation import recitation_router
from app.utils.config import config
from app.utils.logger import get_logger

logger = get_logger(__name__)

app = FastAPI(
    title="Quran Recitation Validator API",
    description=(
        "Real-time Quran recitation validation — Arabic STT + word-by-word "
        "alignment against Uthmani script."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(ws_router)
app.include_router(recitation_router)


@app.on_event("startup")
async def startup():
    logger.info("Quran Recitation Validator starting up")
    logger.info(f"  Whisper model  : {config.WHISPER_MODEL_SIZE}")
    logger.info(f"  Match threshold: {config.MATCH_THRESHOLD}")
    logger.info(f"  Lookahead      : {config.LOOKAHEAD_WORDS} words")

def run_terminal():
    """Terminal-only mode — no server, no frontend."""

    from app.services.recitation_service import RecitationService

    service = RecitationService(
        surah_file=config.DEFAULT_SURAH_FILE,
        model_size=config.WHISPER_MODEL_SIZE,
    )

    sm = service.state_manager

    print("\n🕌 Quran Recitation Validator\n")
    print(f"   Loaded  : {config.DEFAULT_SURAH_FILE}")

    # ✅ Total words calculation
    total_words = sm.total_words()
    print(f"   Words   : {total_words}")

    print(f"   Whisper : {config.WHISPER_MODEL_SIZE}\n")
    print("-" * 60)

    # ---------------- LOOP ---------------- #

    while not sm.is_finished():

        state = sm.get_current_position()
        ayah_idx = state["ayah_index"]
        word_idx = state["word_index"]
        expected_word = state["word"]

        print(f"\n▶  Ayah {ayah_idx + 1} | Expecting: {expected_word}")

        results = service.process_audio_input()

        print(f"\n  {'Spoken':<22} {'Expected':<22} {'Status':<12}")
        print("  " + "-" * 60)

        for r in results:
            status = r.get("status", "")

            if status == "correct":
                print(f"  {r['spoken']:<22} {r['expected']:<22} ✅")

            elif status == "incorrect":
                print(f"  {r['spoken']:<22} {r['expected']:<22} ❌")

            elif status == "extra":
                print(f"  {r['spoken']:<22} {'-':<22} 🚨 EXTRA")

            elif status == "missed":
                print(f"  {'-':<22} {r['expected']:<22} ⚠️ MISSED")

            elif status == "jump":
                print(f"  {r.get('spoken', '-'): <22} {'-':<22} ⏭ JUMP")

                missed = r.get("missed", [])
                if missed:
                    print(f"     Missed: {' '.join(missed)}")

            elif status == "empty":
                print(f"  {'-':<22} {'-':<22} ⚠️ No speech detected")

            elif status == "noise":
                print(f"  {'-':<22} {'-':<22} ⚠️ Noise / unclear speech")

            else:
                print(f"  {r}")

    print("\n✅ Surah complete!\n")    


def _is_finished(nav, quran_data):
    return nav.current_ayah_index >= len(quran_data)
    
if __name__ == "__main__":
    import sys
    if "--terminal-stream" in sys.argv:
        # New concurrent/streaming mode (non-blocking)
        from app.services.recitation_service import RecitationService
        from app.audio.recorder import record_audio
        from app.streaming.concurrent_session import ConcurrentRecitationSession

        service = RecitationService(
            surah_file=config.DEFAULT_SURAH_FILE,
            model_size=config.WHISPER_MODEL_SIZE,
        )

        # Create a simple recorder wrapper
        class SimpleRecorder:
            def record_until_pause(self):
                return record_audio()

        recorder = SimpleRecorder()

        print("\n🕌 Quran Recitation Validator (Streaming Mode)\n")
        print(f"   Loaded  : {config.DEFAULT_SURAH_FILE}")
        print(f"   Words   : {service.state_manager.total_words()}")
        print(f"   Whisper : {config.WHISPER_MODEL_SIZE}\n")
        print("-" * 60)

        # Start concurrent session
        session = ConcurrentRecitationSession(
            service=service,
            recorder=recorder,
        )
        session.start()

        print("\n✅ Surah complete!\n")

    elif "--terminal" in sys.argv:
        # Old blocking/synchronous mode (stable)
        run_terminal()
    else:
        # Web server mode
        uvicorn.run(
            "app.main:app",
            host=config.HOST,
            port=config.PORT,
            reload=config.DEBUG,
        )
