import json

from app.bridge.session import (
    VoiceBridgeSession,
    _UtteranceBuffer,
    _normalized_pcm16_rms,
    _text_similarity,
)
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


def test_echo_similarity_tolerates_minor_asr_differences() -> None:
    assert _text_similarity("今天天气很好我们出去走走", "今天天气真好我们一起出去走走吧") > 0.6
    assert _text_similarity("关闭提醒", "今天天气真好我们一起出去走走吧") < 0.3


def test_utterance_buffer_holds_and_combines_asr_sentence_ends() -> None:
    utterance = _UtteranceBuffer()

    assert utterance.update("我觉得第一个问题是承担比较低", True) == "我觉得第一个问题是承担比较低"
    assert utterance.update("第二个问题是团队士气比较差", True) == (
        "我觉得第一个问题是承担比较低 第二个问题是团队士气比较差"
    )
    assert utterance.final_segment_count == 2
    assert utterance.consume() == "我觉得第一个问题是承担比较低 第二个问题是团队士气比较差"
    assert utterance.text == ""


def test_utterance_buffer_replaces_cumulative_asr_results() -> None:
    utterance = _UtteranceBuffer()

    utterance.update("hello", True)
    assert utterance.update("hello world", True) == "hello world"
    assert utterance.update("next thought", False) == "hello world next thought"
