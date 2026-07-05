from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
import ssl

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


TTS_VOICE_GROUPS: list[dict[str, object]] = [
    {
        "label": "女声",
        "voices": [
            {
                "value": "Cherry",
                "name": "芊悦",
                "description": "阳光积极、亲切自然的小姐姐音色。",
            },
            {"value": "Serena", "name": "苏瑶", "description": "温柔自然的小姐姐音色。"},
            {
                "value": "Jennifer",
                "name": "詹妮弗",
                "description": "品牌级、电影质感般的美语女声。",
            },
            {"value": "Maia", "name": "四月", "description": "知性与温柔兼具的女声。"},
            {
                "value": "Sohee",
                "name": "素熙",
                "description": "温柔开朗、情绪丰富的韩系女声。",
            },
            {"value": "Sunny", "name": "四川-晴儿", "description": "甜美亲切的四川女声。"},
        ],
    },
    {
        "label": "男声",
        "voices": [
            {
                "value": "Ethan",
                "name": "晨煦",
                "description": "标准普通话，带部分北方口音，阳光温暖、有活力。",
            },
            {
                "value": "Nofish",
                "name": "不吃鱼",
                "description": "不会翘舌音的设计师男声。",
            },
            {
                "value": "Ryan",
                "name": "甜茶",
                "description": "节奏感强、戏感鲜明、真实有张力的男声。",
            },
            {"value": "Bodega", "name": "博德加", "description": "热情的西班牙大叔音色。"},
            {
                "value": "Andre",
                "name": "安德雷",
                "description": "声音磁性、自然舒服、沉稳的男声。",
            },
            {
                "value": "Radio Gol",
                "name": "拉迪奥·戈尔",
                "description": "足球解说风格，情绪饱满、有现场感。",
            },
            {
                "value": "Dylan",
                "name": "北京-晓东",
                "description": "胡同里长大的北京小伙儿音色。",
            },
            {
                "value": "Rocky",
                "name": "粤语-阿强",
                "description": "幽默风趣的粤语男声，适合轻松陪聊。",
            },
        ],
    },
]
TTS_VOICE_OPTIONS: dict[str, str] = {
    str(voice["value"]): str(voice["name"])
    for group in TTS_VOICE_GROUPS
    for voice in group["voices"]  # type: ignore[index]
}
MIN_TTS_SPEECH_RATE = 0.5
MAX_TTS_SPEECH_RATE = 2.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    public_base_url: AnyHttpUrl | str = "http://127.0.0.1:8080"
    cors_allow_origins: str = ""
    strict_config_validation: bool = False
    app_shared_secret: str = Field(min_length=16)
    jwt_secret: str = Field(min_length=16)
    jwt_ttl_seconds: int = 900
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=365)
    bootstrap_admin_username: str = "admin"
    allow_legacy_shared_secret_auth: bool = False
    auth_rate_limit_requests: int = 20
    auth_rate_limit_window_seconds: int = 60
    auth_lockout_failures: int = Field(default=5, ge=3, le=100)
    auth_lockout_seconds: int = Field(default=900, ge=60, le=86400)
    client_log_rate_limit_requests: int = 120
    client_log_rate_limit_window_seconds: int = 60
    monitoring_token: str | None = None
    dependency_health_cache_seconds: float = Field(default=5, ge=0, le=60)
    conversation_database_path: str = "data/conversations.sqlite3"
    max_concurrent_sessions: int = Field(default=8, ge=1, le=1000)

    hermes_base_url: str = "http://127.0.0.1:8000"
    hermes_api_key: str | None = None
    hermes_model: str = "hermes"
    hermes_timeout_seconds: float = 45
    hermes_max_attempts: int = Field(default=2, ge=1, le=5)
    hermes_retry_backoff_seconds: float = Field(default=0.4, ge=0, le=10)
    hermes_max_tokens: int = Field(default=1024, ge=100, le=4096)
    hermes_history_max_turns: int = Field(default=12, ge=1, le=100)
    hermes_history_max_chars: int = Field(default=24000, ge=1000, le=200000)
    hermes_system_prompt: str | None = (
        "你是Hermes，一个语音助手。你在与用户进行持续对话，请记住之前的对话内容。"
        "回复将直接朗读，禁止Markdown格式，用自然口语短句。"
        "回答简洁，控制在150字以内，但不要为了简短而牺牲回答质量。"
    )

    dashscope_api_key: str | None = None
    dashscope_asr_model: str = "fun-asr-realtime"
    dashscope_asr_ws_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    dashscope_tts_model: str = "qwen3-tts-flash-realtime"
    dashscope_tts_ws_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    dashscope_tts_voice: str = "Cherry"
    dashscope_tts_speech_rate: float = 1.0
    dashscope_tts_audio_timeout_seconds: float = 90
    dashscope_control_timeout_seconds: float = Field(default=12, ge=1, le=120)

    use_mock_asr: bool = False
    use_mock_tts: bool = False
    pwa_max_upload_bytes: int = 10_000_000
    webrtc_audio_prebuffer_seconds: float = Field(default=1.0, ge=0.1, le=2.0)
    webrtc_adaptive_buffer_enabled: bool = True
    webrtc_audio_prebuffer_min_seconds: float = Field(default=0.5, ge=0.1, le=2.0)
    webrtc_audio_prebuffer_max_seconds: float = Field(default=1.2, ge=0.1, le=2.0)
    webrtc_rebuffer_step_seconds: float = Field(default=0.2, ge=0.05, le=1.0)
    webrtc_session_idle_timeout_seconds: int = Field(default=45, ge=30, le=3600)
    auto_vad_enabled: bool = True
    auto_vad_rms_threshold: float = 0.012
    auto_vad_silence_ms: int = 2500
    auto_vad_min_speech_ms: int = 80
    auto_vad_preroll_ms: int = Field(default=500, ge=200, le=1500)
    barge_in_min_chars: int = 3
    barge_in_cooldown_ms: int = 500
    barge_in_echo_similarity: float = Field(default=0.62, ge=0.3, le=1.0)
    barge_in_acoustic_echo_similarity: float = Field(default=0.35, ge=0.1, le=1.0)

    ice_stun_urls: str = "stun:stun.l.google.com:19302"
    ice_turn_urls: str = ""
    ice_turn_internal_urls: str = ""
    ice_turn_username: str = ""
    ice_turn_credential: str = ""

    ssl_cert_file: str = "../ssl/fullchain.pem"
    ssl_key_file: str = "../ssl/privkey.pem"

    @property
    def ice_servers(self) -> list[dict[str, object]]:
        return self._ice_servers(self.ice_turn_urls)

    @property
    def server_ice_servers(self) -> list[dict[str, object]]:
        return self._ice_servers(self.ice_turn_internal_urls or self.ice_turn_urls)

    def _ice_servers(self, turn_urls: str) -> list[dict[str, object]]:
        servers: list[dict[str, object]] = []
        if self.ice_stun_urls:
            servers.append({"urls": [url.strip() for url in self.ice_stun_urls.split(",") if url.strip()]})
        if turn_urls:
            servers.append(
                {
                    "urls": [url.strip() for url in turn_urls.split(",") if url.strip()],
                    "username": self.ice_turn_username,
                    "credential": self.ice_turn_credential,
                }
            )
        return servers

    @property
    def cors_origins(self) -> list[str]:
        if self.cors_allow_origins:
            return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]
        return [str(self.public_base_url).rstrip("/")]

    @property
    def turn_configured(self) -> bool:
        return bool(self.ice_turn_urls and self.ice_turn_username and self.ice_turn_credential)

    @property
    def turn_config_warning(self) -> str | None:
        has_any = bool(self.ice_turn_urls or self.ice_turn_username or self.ice_turn_credential)
        if not has_any:
            return "TURN is not configured; cellular or restrictive NAT networks may be unstable."
        if not self.turn_configured:
            return "TURN config is incomplete; set ICE_TURN_URLS, ICE_TURN_USERNAME, and ICE_TURN_CREDENTIAL."
        return None

    def diagnostics(self) -> dict[str, object]:
        checks: dict[str, dict[str, object]] = {
            "dashscope_api_key": {
                "ok": bool(self.use_mock_asr and self.use_mock_tts) or bool(self.dashscope_api_key),
                "detail": "configured" if self.dashscope_api_key else "missing unless both mocks are enabled",
            },
            "hermes_base_url": {"ok": bool(self.hermes_base_url), "detail": self.hermes_base_url},
            "jwt_secret": {"ok": len(self.jwt_secret) >= 16, "detail": "min_length=16"},
            "app_shared_secret": {"ok": len(self.app_shared_secret) >= 16, "detail": "min_length=16"},
            "turn": {
                "ok": self.turn_configured,
                "detail": self.turn_config_warning or "configured",
            },
            "ssl_cert_file": {
                **_ssl_cert_check(self.ssl_cert_file),
            },
            "ssl_key_file": {
                "ok": Path(self.ssl_key_file).exists(),
                "detail": self.ssl_key_file,
            },
        }
        errors = [
            name
            for name, check in checks.items()
            if not check["ok"] and name not in {"turn", "ssl_key_file"}
        ]
        warnings = [
            name
            for name, check in checks.items()
            if not check["ok"] and name in {"turn", "ssl_key_file"}
        ]
        return {"ok": not errors, "checks": checks, "errors": errors, "warnings": warnings}

    def strict_errors(self) -> list[str]:
        diagnostics = self.diagnostics()
        errors = list(diagnostics["errors"])
        if not self.turn_configured:
            errors.append("turn")
        return errors


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _ssl_cert_check(cert_file: str) -> dict[str, object]:
    path = Path(cert_file)
    if not path.exists():
        return {"ok": False, "detail": f"{cert_file} missing"}
    try:
        decoded = ssl._ssl._test_decode_cert(str(path))  # type: ignore[attr-defined]
        not_after = datetime.strptime(decoded["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    except Exception as exc:
        return {"ok": False, "detail": f"{cert_file} unreadable: {exc}"}
    now = datetime.now(UTC)
    remaining = not_after - now
    if remaining.total_seconds() <= 0:
        return {"ok": False, "detail": f"{cert_file} expired at {not_after.isoformat()}"}
    return {
        "ok": True,
        "detail": f"{cert_file} expires at {not_after.isoformat()} ({remaining.days} days left)",
    }
