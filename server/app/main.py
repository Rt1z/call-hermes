import asyncio
from contextlib import asynccontextmanager, suppress
import json
import logging
import hmac
import sqlite3
from pathlib import Path
import time
from typing import Annotated, Any

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import create_session_token, verify_bearer_identity
from app.account_store import AccountStore
from app.bridge.session import VoiceBridgeSession
from app.config import (
    MAX_TTS_SPEECH_RATE,
    MIN_TTS_SPEECH_RATE,
    TTS_VOICE_GROUPS,
    TTS_VOICE_OPTIONS,
    Settings,
    get_settings,
)
from app.circuit_breaker import asr_breaker, hermes_breaker, tts_breaker
from app.conversation_store import ConversationStore
from app.integrations.hermes import HermesClient
from app.logging_config import configure_logging
from app.metrics import runtime_metrics
from app.pwa.routes import router as pwa_router
from app.rate_limit import (
    clear_auth_failures,
    enforce_auth_rate_limit,
    enforce_client_log_rate_limit,
    record_auth_failure,
)


logger = logging.getLogger("call_hermes.main")


class AuthRequest(BaseModel):
    shared_secret: str
    device_name: str | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)
    device_name: str | None = Field(default=None, max_length=200)
    device_id: str | None = Field(default=None, max_length=64)


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100, pattern=r"^[\w.@+-]+$")
    password: str = Field(min_length=12, max_length=1024)
    role: str = Field(default="user", pattern=r"^(user|admin)$")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class OfferRequest(BaseModel):
    sdp: str
    type: str = "offer"
    preserve_conversation: bool = True
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    tts_voice: str | None = None
    tts_speech_rate: float | None = None
    vad_silence_ms: int | None = None
    hermes_model: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[\w./:-]+$"
    )
    system_prompt: str | None = Field(default=None, min_length=1, max_length=4000)
    max_tokens: int | None = Field(default=None, ge=100, le=4096)
    history_max_turns: int | None = Field(default=None, ge=1, le=100)
    language: str | None = Field(default=None, pattern=r"^(auto|zh-CN|en-US)$")


class OfferResponse(BaseModel):
    sdp: str
    type: str
    ice_servers: list[dict[str, object]]


class ClientLogRequest(BaseModel):
    level: str = "info"
    message: str = Field(min_length=1, max_length=500)
    details: dict[str, Any] = Field(default_factory=dict)
    url: str | None = Field(default=None, max_length=2048)
    user_agent: str | None = Field(default=None, max_length=512)
    ts: str | None = Field(default=None, max_length=64)


class ConversationMetadataRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    favorite: bool | None = None
    archived: bool | None = None


sessions: dict[str, VoiceBridgeSession] = {}
session_ids_in_flight: set[str] = set()
session_registry_lock = asyncio.Lock()
conversation_store: ConversationStore | None = None
account_store: AccountStore | None = None
hermes_health_lock = asyncio.Lock()
hermes_health_cached: tuple[bool, str] | None = None
hermes_health_expires_at = 0.0


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global account_store, conversation_store
    configure_logging()
    settings = get_settings()
    conversation_store = ConversationStore(settings.conversation_database_path)
    conversation_store.initialize()
    account_store = AccountStore(settings.conversation_database_path)
    account_store.initialize(settings.bootstrap_admin_username, settings.app_shared_secret)
    _app.state.conversation_store = conversation_store
    _app.state.account_store = account_store
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
    cleanup_task = asyncio.create_task(_cleanup_stale_sessions(settings))
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
        for session in list(sessions.values()):
            await session.close()


app = FastAPI(title="Call Hermes Voice Bridge", version="0.1.0", lifespan=lifespan)
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(pwa_router)


@app.middleware("http")
async def no_store_static_assets(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".js", ".css", ".webmanifest")):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), payment=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "media-src 'self' blob:; connect-src 'self' https: wss:; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'"
    )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


async def _health_details(settings: Settings) -> dict[str, object]:
    hermes_ok, hermes_detail = await _cached_hermes_health(settings)
    asr_ok = bool(settings.use_mock_asr or settings.dashscope_api_key)
    tts_ok = bool(settings.use_mock_tts or settings.dashscope_api_key)
    config = settings.diagnostics()
    database_ok, database_detail = _conversation_store().health()
    return {
        "ok": hermes_ok and asr_ok and tts_ok and database_ok and bool(config["ok"]),
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
            "rebuffer_step_seconds": settings.webrtc_rebuffer_step_seconds,
            "auto_vad_enabled": settings.auto_vad_enabled,
            "auto_vad_rms_threshold": settings.auto_vad_rms_threshold,
            "auto_vad_silence_ms": settings.auto_vad_silence_ms,
            "auto_vad_preroll_ms": settings.auto_vad_preroll_ms,
            "barge_in_min_chars": settings.barge_in_min_chars,
            "session_idle_timeout_seconds": settings.webrtc_session_idle_timeout_seconds,
            "max_concurrent_sessions": settings.max_concurrent_sessions,
        },
        "config": config,
        "database": {"ok": database_ok, "detail": database_detail},
        "active_sessions": len(sessions),
    }


@app.get("/health")
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
    details = await _health_details(settings)
    return _public_health(details)


@app.get("/health/details")
async def health_details(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    _require_monitor_access(request, settings, authorization)
    return await _health_details(settings)


def _public_health(details: dict[str, object]) -> dict[str, object]:
    return {
        "ok": details["ok"],
        "components": {
            name: bool(component.get("ok"))
            for name, component in details.items()
            if name in {"hermes", "asr", "tts", "database"} and isinstance(component, dict)
        },
    }


@app.get("/live")
async def live() -> dict[str, bool]:
    return {"ok": True}


@app.get("/ready")
async def ready(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
    return _public_health(await _health_details(settings))


@app.get("/metrics")
async def metrics(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    _require_monitor_access(request, settings, authorization)
    snapshot = runtime_metrics.snapshot()
    snapshot["gauges"] = {"active_sessions": len(sessions)}
    snapshot["circuits"] = {
        "hermes": hermes_breaker.snapshot(),
        "asr": asr_breaker.snapshot(),
        "tts": tts_breaker.snapshot(),
    }
    return snapshot


@app.get("/metrics/prometheus", response_class=PlainTextResponse)
async def prometheus_metrics(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    _require_monitor_access(request, settings, authorization)
    snapshot = runtime_metrics.snapshot()
    lines: list[str] = []
    for name, value in snapshot["counters"].items():  # type: ignore[union-attr]
        lines.append(f"call_hermes_{name}_total {value}")
    for name, values in snapshot["observations"].items():  # type: ignore[union-attr]
        lines.extend(
            [
                f"call_hermes_{name}_count {values['count']}",
                f"call_hermes_{name}_sum {values['total']}",
                f"call_hermes_{name}_max {values['max']}",
                f"call_hermes_{name}_last {values['last']}",
            ]
        )
    lines.append(f"call_hermes_active_sessions {len(sessions)}")
    for name, breaker in (("hermes", hermes_breaker), ("asr", asr_breaker), ("tts", tts_breaker)):
        state = breaker.snapshot()
        lines.append(f'call_hermes_circuit_open{{provider="{name}"}} {int(bool(state["open"]))}')
        lines.append(f'call_hermes_circuit_failures{{provider="{name}"}} {state["failures"]}')
    return "\n".join(lines) + "\n"


async def _cached_hermes_health(settings: Settings) -> tuple[bool, str]:
    global hermes_health_cached, hermes_health_expires_at
    now = time.monotonic()
    if hermes_health_cached is not None and now < hermes_health_expires_at:
        runtime_metrics.increment("dependency_health_cache_hits")
        return hermes_health_cached
    async with hermes_health_lock:
        now = time.monotonic()
        if hermes_health_cached is not None and now < hermes_health_expires_at:
            runtime_metrics.increment("dependency_health_cache_hits")
            return hermes_health_cached
        runtime_metrics.increment("dependency_health_cache_misses")
        hermes_health_cached = await HermesClient(settings).health()
        hermes_health_expires_at = now + settings.dependency_health_cache_seconds
        return hermes_health_cached


def _complete_login(
    request: Request,
    response: Response,
    settings: Settings,
    user: dict[str, str],
    device_name: str | None,
    device_id: str | None,
) -> dict[str, str]:
    store = _account_store()
    active_device_id = store.register_device(user["id"], device_name or "Unknown device", device_id)
    store.revoke_device_tokens(active_device_id)
    refresh_token, refresh_expires = store.issue_refresh_token(
        user["id"], active_device_id, settings.refresh_token_ttl_days
    )
    _set_refresh_cookie(response, refresh_token, settings)
    access = create_session_token(
        settings,
        device_name,
        user_id=user["id"],
        username=user["username"],
        role=user["role"],
        device_id=active_device_id,
    )
    store.audit("login_succeeded", user["id"], active_device_id, _client_ip(request))
    return {**access, "device_id": active_device_id, "refresh_expires_at": refresh_expires}


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        "hermes_refresh",
        token,
        max_age=settings.refresh_token_ttl_days * 86400,
        httponly=True,
        secure=str(settings.public_base_url).startswith("https://"),
        samesite="strict",
        path="/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie("hermes_refresh", path="/auth", samesite="strict")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _require_monitor_access(
    request: Request, settings: Settings, authorization: str | None
) -> None:
    if request.client and request.client.host in {"127.0.0.1", "::1"}:
        return
    expected = settings.monitoring_token
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Monitoring access denied"
        )


def _active_identity(settings: Settings, authorization: str | None):  # type: ignore[no-untyped-def]
    identity = verify_bearer_identity(settings, authorization)
    if identity.device_id != "legacy" and not _account_store().device_active(
        identity.user_id, identity.device_id
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Device access revoked"
        )
    return identity


def _account_store() -> AccountStore:
    if account_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Account store unavailable"
        )
    return account_store


@app.post("/auth/session")
async def auth_session(
    request: Request,
    response: Response,
    body: AuthRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    enforce_auth_rate_limit(request, settings)
    if not settings.allow_legacy_shared_secret_auth:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Legacy shared-secret authentication is disabled; use /auth/login",
        )
    if not hmac.compare_digest(body.shared_secret, settings.app_shared_secret):
        record_auth_failure(request)
        _account_store().audit("login_failed", ip_address=_client_ip(request))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid shared secret"
        )
    user = _account_store().get_user(settings.bootstrap_admin_username)
    if user is None:
        record_auth_failure(request)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bootstrap user unavailable"
        )
    clear_auth_failures(request)
    return _complete_login(request, response, settings, user, body.device_name, None)


@app.post("/auth/login")
async def auth_login(
    request: Request,
    response: Response,
    body: LoginRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    enforce_auth_rate_limit(request, settings)
    user = _account_store().authenticate(body.username.strip(), body.password)
    if user is None:
        record_auth_failure(request)
        _account_store().audit(
            "login_failed",
            ip_address=_client_ip(request),
            details={"username": body.username[:100]},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password"
        )
    clear_auth_failures(request)
    return _complete_login(request, response, settings, user, body.device_name, body.device_id)


@app.post("/auth/refresh")
async def auth_refresh(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    hermes_refresh: Annotated[str | None, Cookie()] = None,
) -> dict[str, str]:
    if not hermes_refresh:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token"
        )
    refreshed = _account_store().consume_refresh_token(hermes_refresh)
    if refreshed is None:
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )
    refresh_token, refresh_expires = _account_store().issue_refresh_token(
        refreshed["user_id"], refreshed["device_id"], settings.refresh_token_ttl_days
    )
    _set_refresh_cookie(response, refresh_token, settings)
    token = create_session_token(
        settings,
        refreshed["name"],
        user_id=refreshed["user_id"],
        username=refreshed["username"],
        role=refreshed["role"],
        device_id=refreshed["device_id"],
    )
    _account_store().audit(
        "token_refreshed", refreshed["user_id"], refreshed["device_id"], _client_ip(request)
    )
    return {**token, "device_id": refreshed["device_id"], "refresh_expires_at": refresh_expires}


@app.post("/auth/logout")
async def auth_logout(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
    hermes_refresh: Annotated[str | None, Cookie()] = None,
) -> dict[str, bool]:
    identity = _active_identity(settings, authorization)
    if hermes_refresh:
        _account_store().revoke_refresh_token(hermes_refresh)
    _account_store().audit("logout", identity.user_id, identity.device_id)
    _clear_refresh_cookie(response)
    return {"ok": True}


@app.get("/auth/devices")
async def list_authorized_devices(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    identity = _active_identity(settings, authorization)
    return {"devices": _account_store().list_devices(identity.user_id, identity.device_id)}


@app.delete("/auth/devices/{device_id}")
async def revoke_authorized_device(
    device_id: str,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    identity = _active_identity(settings, authorization)
    revoked = _account_store().revoke_device(identity.user_id, device_id)
    _account_store().audit(
        "device_revoked",
        identity.user_id,
        device_id,
        _client_ip(request),
        {"by": identity.device_id},
    )
    for active_id, session in list(sessions.items()):
        if getattr(session, "device_id", None) == device_id:
            sessions.pop(active_id, None)
            await session.terminate()
    return {"ok": revoked}


@app.get("/auth/audit")
async def list_auth_audit(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    identity = _active_identity(settings, authorization)
    return {"events": _account_store().list_audit(identity.user_id)}


@app.get("/auth/users")
async def list_users(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    identity = _active_identity(settings, authorization)
    if identity.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator required")
    return {"users": _account_store().list_users()}


@app.post("/auth/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    identity = _active_identity(settings, authorization)
    if identity.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator required")
    try:
        user = _account_store().create_user(body.username, body.password, body.role)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Username already exists"
        ) from exc
    _account_store().audit(
        "user_created",
        identity.user_id,
        identity.device_id,
        _client_ip(request),
        {"username": body.username},
    )
    return user


@app.post("/auth/password")
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    identity = _active_identity(settings, authorization)
    if not _account_store().change_password(
        identity.user_id, body.current_password, body.new_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect"
        )
    _account_store().audit(
        "password_changed", identity.user_id, identity.device_id, _client_ip(request)
    )
    return {"ok": True}


@app.post("/client/log")
async def client_log(
    request: Request,
    body: ClientLogRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, bool]:
    enforce_client_log_rate_limit(request, settings)
    if len(json.dumps(body.details, ensure_ascii=False, default=str)) > 8192:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Client log details too large"
        )
    level = body.level.lower()
    log_method = (
        logger.warning if level == "warn" else logger.error if level == "error" else logger.info
    )
    log_method(
        "client level=%s message=%s details=%s url=%s user_agent=%s ts=%s",
        body.level,
        body.message,
        _redact_log_value(body.details),
        body.url,
        body.user_agent,
        body.ts,
    )
    return {"ok": True}


def _redact_log_value(value: object) -> object:
    sensitive = {"authorization", "token", "password", "secret", "api_key", "cookie"}
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if any(part in key.lower() for part in sensitive)
            else _redact_log_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_log_value(item) for item in value[:100]]
    return value


@app.post("/rtc/offer", response_model=OfferResponse)
async def rtc_offer(
    body: OfferRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> OfferResponse:
    identity = _active_identity(settings, authorization)
    session_id = identity.session_id
    stale_device_sessions: list[VoiceBridgeSession] = []
    async with session_registry_lock:
        if session_id in session_ids_in_flight:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A connection attempt is already in progress for this session",
            )
        old = sessions.pop(session_id, None)
        for active_session_id, active_session in list(sessions.items()):
            if (
                active_session.user_id == identity.user_id
                and active_session.device_id == identity.device_id
            ):
                sessions.pop(active_session_id, None)
                stale_device_sessions.append(active_session)
        projected_sessions = len(set(sessions) | session_ids_in_flight | {session_id})
        if (
            old is None
            and not stale_device_sessions
            and projected_sessions > settings.max_concurrent_sessions
        ):
            runtime_metrics.increment("rtc_sessions_rejected_capacity")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Voice service is at capacity",
                headers={"Retry-After": "5"},
            )
        session_ids_in_flight.add(session_id)

    conversation_id = body.conversation_id or session_id
    store = _conversation_store()
    session: VoiceBridgeSession | None = None
    try:
        for stale_session in stale_device_sessions:
            logger.info(
                "closing stale device session old_session_id=%s new_session_id=%s device_id=%s",
                stale_session.session_id,
                session_id,
                identity.device_id,
            )
            await stale_session.close()
        if body.preserve_conversation:
            conversation_history = (
                old.conversation_history if old else store.load(conversation_id, identity.user_id)
            )
        else:
            conversation_history = []
            store.clear(conversation_id, identity.user_id)
        if old:
            await old.close()

        session_settings = _settings_for_offer(settings, body)
        logger.info(
            "session created session_id=%s vad_silence_ms=%d tts_voice=%s",
            session_id,
            session_settings.auto_vad_silence_ms,
            session_settings.dashscope_tts_voice,
        )
        session = VoiceBridgeSession(
            session_id,
            session_settings,
            conversation_history=conversation_history,
            on_closed=lambda closed: _remove_closed_session(session_id, closed),
            on_history_updated=lambda messages: store.save(
                conversation_id, messages, identity.user_id
            ),
            on_metric=_record_session_metric,
            device_name=identity.device_name,
            device_id=identity.device_id,
            user_id=identity.user_id,
        )
        runtime_metrics.increment("rtc_sessions_created")
        sessions[session_id] = session
        answer = await session.answer(body.sdp, body.type)
        return OfferResponse(**answer, ice_servers=session_settings.ice_servers)
    except Exception:
        runtime_metrics.increment("rtc_session_create_errors")
        if session is not None:
            if sessions.get(session_id) is session:
                sessions.pop(session_id, None)
            await session.close()
        raise
    finally:
        async with session_registry_lock:
            session_ids_in_flight.discard(session_id)


@app.delete("/rtc/session")
async def close_rtc_session(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    session_id = _active_identity(settings, authorization).session_id
    session = sessions.pop(session_id, None)
    if session:
        await session.close()
    return {"ok": True}


@app.get("/rtc/sessions")
async def list_rtc_sessions(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    identity = _active_identity(settings, authorization)
    current_session_id = identity.session_id
    return {
        "sessions": [
            session.summary(current_session_id)
            for session in sorted(sessions.values(), key=lambda item: item.started_at, reverse=True)
            if session.user_id == identity.user_id
        ]
    }


@app.delete("/rtc/sessions/{session_id}")
async def terminate_rtc_session(
    session_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    identity = _active_identity(settings, authorization)
    session = sessions.pop(session_id, None)
    if session and session.user_id == identity.user_id:
        await session.terminate()
        runtime_metrics.increment("rtc_sessions_terminated")
    return {"ok": True}


def _remove_closed_session(session_id: str, closed: VoiceBridgeSession) -> None:
    if sessions.get(session_id) is closed:
        sessions.pop(session_id, None)
        logger.info("session removed session_id=%s", session_id)
        runtime_metrics.increment("rtc_sessions_closed")


def _record_session_metric(name: str, value: float) -> None:
    if name.endswith("_ms"):
        runtime_metrics.observe(name, value)
    else:
        runtime_metrics.increment(name, int(value))


def _conversation_store() -> ConversationStore:
    if conversation_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Conversation store unavailable"
        )
    return conversation_store


@app.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    identity = _active_identity(settings, authorization)
    return {
        "conversation_id": conversation_id,
        "messages": _conversation_store().load(conversation_id, identity.user_id),
    }


@app.get("/conversations")
async def list_conversations(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
    query: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    identity = _active_identity(settings, authorization)
    return {
        "conversations": _conversation_store().list(
            query=query.strip(), limit=limit, offset=offset, owner_id=identity.user_id
        )
    }


@app.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    body: ConversationMetadataRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    identity = _active_identity(settings, authorization)
    updated = _conversation_store().update_metadata(
        conversation_id,
        identity.user_id,
        title=body.title,
        favorite=body.favorite,
        archived=body.archived,
    )
    return {"ok": updated}


@app.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    identity = _active_identity(settings, authorization)
    _conversation_store().clear(conversation_id, identity.user_id)
    return {"ok": True}


async def _cleanup_stale_sessions(settings: Settings) -> None:
    timeout = settings.webrtc_session_idle_timeout_seconds
    while True:
        await asyncio.sleep(10)
        cutoff = time.monotonic() - timeout
        stale = [
            (session_id, session)
            for session_id, session in sessions.items()
            if session.last_activity_at < cutoff
        ]
        for session_id, session in stale:
            if sessions.get(session_id) is not session:
                continue
            sessions.pop(session_id, None)
            logger.warning(
                "stale session removed session_id=%s idle_timeout=%ss", session_id, timeout
            )
            runtime_metrics.increment("rtc_sessions_stale")
            await session.close()


@app.get("/rtc/config")
async def rtc_config(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    _active_identity(settings, authorization)
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
    update: dict[str, object] = {}
    _apply_tts_overrides(settings, offer, update)
    _apply_vad_overrides(offer, update)
    _apply_assistant_overrides(offer, update)
    if not update:
        return settings
    return settings.model_copy(update=update)


def _apply_tts_overrides(
    settings: Settings, offer: OfferRequest, update: dict[str, object]
) -> None:
    tts_voice = offer.tts_voice
    tts_speech_rate = offer.tts_speech_rate
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


VAD_SILENCE_MIN_MS = 500
VAD_SILENCE_MAX_MS = 5000


def _apply_vad_overrides(offer: OfferRequest, update: dict[str, object]) -> None:
    vad_silence_ms = offer.vad_silence_ms
    if vad_silence_ms is not None:
        if not VAD_SILENCE_MIN_MS <= vad_silence_ms <= VAD_SILENCE_MAX_MS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"VAD silence must be between {VAD_SILENCE_MIN_MS} and {VAD_SILENCE_MAX_MS} ms",
            )
        update["auto_vad_silence_ms"] = vad_silence_ms


def _apply_assistant_overrides(offer: OfferRequest, update: dict[str, object]) -> None:
    if offer.hermes_model is not None:
        update["hermes_model"] = offer.hermes_model
    if offer.system_prompt is not None:
        prompt = offer.system_prompt.strip()
        if offer.language == "zh-CN":
            prompt += "\n请优先使用简体中文回答。"
        elif offer.language == "en-US":
            prompt += "\nReply in English unless the user explicitly requests another language."
        update["hermes_system_prompt"] = prompt
    if offer.max_tokens is not None:
        update["hermes_max_tokens"] = offer.max_tokens
    if offer.history_max_turns is not None:
        update["hermes_history_max_turns"] = offer.history_max_turns


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="pwa")
