from contextlib import asynccontextmanager
import logging
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import create_session_token, verify_bearer_token
from app.bridge.session import VoiceBridgeSession
from app.config import (
    MAX_TTS_SPEECH_RATE,
    MIN_TTS_SPEECH_RATE,
    TTS_VOICE_GROUPS,
    TTS_VOICE_OPTIONS,
    Settings,
    get_settings,
)
from app.integrations.hermes import HermesClient
from app.logging_config import configure_logging
from app.pwa.routes import router as pwa_router
from app.rate_limit import enforce_auth_rate_limit


logger = logging.getLogger("call_hermes.main")


class AuthRequest(BaseModel):
    shared_secret: str
    device_name: str | None = None


class OfferRequest(BaseModel):
    sdp: str
    type: str = "offer"
    tts_voice: str | None = None
    tts_speech_rate: float | None = None


class OfferResponse(BaseModel):
    sdp: str
    type: str
    ice_servers: list[dict[str, object]]


class ClientLogRequest(BaseModel):
    level: str = "info"
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    url: str | None = None
    user_agent: str | None = None
    ts: str | None = None


sessions: dict[str, VoiceBridgeSession] = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    settings = get_settings()
    strict_errors = settings.strict_errors()
    if settings.strict_config_validation and strict_errors:
        raise RuntimeError(f"Strict config validation failed: {', '.join(strict_errors)}")
    if settings.turn_config_warning:
        logger.warning(settings.turn_config_warning)
    else:
        logger.info("TURN configured urls=%s", settings.ice_turn_urls)
    logger.info(
        "voice settings prebuffer=%.2fs vad_preroll_ms=%d "
        "barge_in_min_chars=%d barge_in_cooldown_ms=%d",
        settings.webrtc_audio_prebuffer_seconds,
        settings.auto_vad_preroll_ms,
        settings.barge_in_min_chars,
        settings.barge_in_cooldown_ms,
    )
    yield
    for session in list(sessions.values()):
        await session.close()


app = FastAPI(title="Call Hermes Voice Bridge", version="0.1.0", lifespan=lifespan)
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(pwa_router)


@app.middleware("http")
async def no_store_static_assets(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".js", ".css", ".webmanifest")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
    hermes_ok, hermes_detail = await HermesClient(settings).health()
    asr_ok = bool(settings.use_mock_asr or settings.dashscope_api_key)
    tts_ok = bool(settings.use_mock_tts or settings.dashscope_api_key)
    config = settings.diagnostics()
    return {
        "ok": hermes_ok and asr_ok and tts_ok and bool(config["ok"]),
        "hermes": {
            "ok": hermes_ok,
            "detail": hermes_detail,
            "history_max_turns": settings.hermes_history_max_turns,
            "history_max_chars": settings.hermes_history_max_chars,
        },
        "asr": {
            "ok": asr_ok,
            "model": settings.dashscope_asr_model,
            "mock": settings.use_mock_asr,
        },
        "tts": {
            "ok": tts_ok,
            "model": settings.dashscope_tts_model,
            "voice": settings.dashscope_tts_voice,
            "speech_rate": settings.dashscope_tts_speech_rate,
            "mock": settings.use_mock_tts,
        },
        "webrtc": {
            "turn_configured": settings.turn_configured,
            "turn_warning": settings.turn_config_warning,
            "ice_servers": len(settings.ice_servers),
            "audio_prebuffer_seconds": settings.webrtc_audio_prebuffer_seconds,
            "adaptive_buffer_enabled": settings.webrtc_adaptive_buffer_enabled,
            "audio_prebuffer_min_seconds": settings.webrtc_audio_prebuffer_min_seconds,
            "audio_prebuffer_max_seconds": settings.webrtc_audio_prebuffer_max_seconds,
            "auto_vad_enabled": settings.auto_vad_enabled,
            "auto_vad_rms_threshold": settings.auto_vad_rms_threshold,
            "auto_vad_silence_ms": settings.auto_vad_silence_ms,
            "auto_vad_preroll_ms": settings.auto_vad_preroll_ms,
            "barge_in_min_chars": settings.barge_in_min_chars,
        },
        "config": config,
        "active_sessions": len(sessions),
    }


@app.post("/auth/session")
async def auth_session(
    request: Request,
    body: AuthRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    enforce_auth_rate_limit(request, settings)
    if body.shared_secret != settings.app_shared_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid shared secret")
    return create_session_token(settings)


@app.post("/client/log")
async def client_log(body: ClientLogRequest) -> dict[str, bool]:
    level = body.level.lower()
    log_method = logger.warning if level == "warn" else logger.error if level == "error" else logger.info
    log_method(
        "client level=%s message=%s details=%s url=%s user_agent=%s ts=%s",
        body.level,
        body.message,
        body.details,
        body.url,
        body.user_agent,
        body.ts,
    )
    return {"ok": True}


@app.post("/rtc/offer", response_model=OfferResponse)
async def rtc_offer(
    body: OfferRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> OfferResponse:
    session_id = verify_bearer_token(settings, authorization)
    old = sessions.pop(session_id, None)
    conversation_history = old.conversation_history if old else []
    if old:
        await old.close()

    session_settings = _settings_for_offer(settings, body)
    session = VoiceBridgeSession(
        session_id,
        session_settings,
        conversation_history=conversation_history,
    )
    sessions[session_id] = session
    answer = await session.answer(body.sdp, body.type)
    return OfferResponse(**answer, ice_servers=session_settings.ice_servers)


@app.get("/rtc/config")
async def rtc_config(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    verify_bearer_token(settings, authorization)
    return {
        "ice_servers": settings.ice_servers,
        "audio": {
            "adaptive_buffer_enabled": settings.webrtc_adaptive_buffer_enabled,
            "prebuffer_seconds": settings.webrtc_audio_prebuffer_seconds,
            "prebuffer_min_seconds": settings.webrtc_audio_prebuffer_min_seconds,
            "prebuffer_max_seconds": settings.webrtc_audio_prebuffer_max_seconds,
        },
        "tts": {
            "voice": settings.dashscope_tts_voice,
            "speech_rate": settings.dashscope_tts_speech_rate,
            "voice_groups": TTS_VOICE_GROUPS,
            "speech_rate_min": MIN_TTS_SPEECH_RATE,
            "speech_rate_max": MAX_TTS_SPEECH_RATE,
            "speech_rate_step": 0.05,
        },
    }


def _settings_for_offer(settings: Settings, offer: OfferRequest) -> Settings:
    return _settings_for_tts(
        settings,
        tts_voice=offer.tts_voice,
        tts_speech_rate=offer.tts_speech_rate,
    )


def _settings_for_tts(
    settings: Settings,
    *,
    tts_voice: str | None,
    tts_speech_rate: float | None,
) -> Settings:
    update: dict[str, object] = {}
    if tts_voice is not None:
        if tts_voice not in TTS_VOICE_OPTIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported TTS voice: {tts_voice}",
            )
        update["dashscope_tts_voice"] = tts_voice
    if tts_speech_rate is not None:
        if not MIN_TTS_SPEECH_RATE <= tts_speech_rate <= MAX_TTS_SPEECH_RATE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"TTS speech rate must be between {MIN_TTS_SPEECH_RATE} "
                    f"and {MAX_TTS_SPEECH_RATE}"
                ),
            )
        update["dashscope_tts_speech_rate"] = tts_speech_rate
    if not update:
        return settings
    return settings.model_copy(update=update)


app.mount("/", StaticFiles(directory="app/static", html=True), name="pwa")
