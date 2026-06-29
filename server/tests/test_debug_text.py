import json

from app.bridge.session import VoiceBridgeSession, _normalized_pcm16_rms
from app.config import Settings


async def test_debug_text_message_submits_text() -> None:
    settings = Settings(app_shared_secret="x" * 32, jwt_secret="y" * 32)
    session = VoiceBridgeSession("test-session", settings)
    submitted: list[str] = []

    async def submit_text(text: str) -> None:
        submitted.append(text)

    session.submit_text = submit_text  # type: ignore[method-assign]
    await session._handle_client_message(json.dumps({"type": "debug_text", "text": "  hello  "}))
    await session.close()

    assert submitted == ["hello"]


async def test_microphone_muted_message_updates_session_state() -> None:
    settings = Settings(app_shared_secret="x" * 32, jwt_secret="y" * 32)
    session = VoiceBridgeSession("test-session", settings)
    events: list[tuple[str, dict[str, object]]] = []

    def emit(event_type: str, **payload: object) -> None:
        events.append((event_type, payload))

    session.events.emit = emit  # type: ignore[method-assign]

    await session._handle_client_message(json.dumps({"type": "microphone_muted", "muted": True}))
    assert session._client_muted is True
    assert events[-1] == ("microphone", {"muted": True})

    await session._handle_client_message(json.dumps({"type": "microphone_muted", "muted": False}))
    await session.close()

    assert session._client_muted is False
    assert events[-1] == ("microphone", {"muted": False})


async def test_network_quality_message_updates_prebuffer() -> None:
    settings = Settings(
        app_shared_secret="x" * 32,
        jwt_secret="y" * 32,
        webrtc_audio_prebuffer_min_seconds=0.3,
        webrtc_audio_prebuffer_max_seconds=1.0,
    )
    session = VoiceBridgeSession("test-session", settings)
    events: list[tuple[str, dict[str, object]]] = []

    def emit(event_type: str, **payload: object) -> None:
        events.append((event_type, payload))

    session.events.emit = emit  # type: ignore[method-assign]
    await session._handle_client_message(
        json.dumps(
            {
                "type": "network_quality",
                "quality": "poor",
                "prebuffer_seconds": 2.0,
            }
        )
    )
    await session.close()

    assert session.output_track.prebuffer_seconds == 1.0
    assert events[-1] == (
        "network_buffer",
        {"quality": "poor", "prebuffer_seconds": 1.0},
    )


async def test_network_quality_message_ignores_invalid_buffer() -> None:
    settings = Settings(app_shared_secret="x" * 32, jwt_secret="y" * 32)
    session = VoiceBridgeSession("test-session", settings)
    initial_seconds = session.output_track.prebuffer_seconds

    await session._handle_client_message(
        json.dumps({"type": "network_quality", "prebuffer_seconds": "not-a-number"})
    )
    await session.close()

    assert session.output_track.prebuffer_seconds == initial_seconds


def test_normalized_pcm16_rms() -> None:
    assert _normalized_pcm16_rms(b"") == 0
    silence = (0).to_bytes(2, "little", signed=True) * 10
    loud = (12000).to_bytes(2, "little", signed=True) * 10
    assert _normalized_pcm16_rms(silence) == 0
    assert 0.35 < _normalized_pcm16_rms(loud) < 0.38
