import asyncio
import json

import app.bridge.session as session_module
from app.bridge.session import (
    VoiceBridgeSession,
    _UtteranceBuffer,
    _normalized_pcm16_rms,
    _text_similarity,
)
from app.config import Settings
from app.integrations.asr import Transcript


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


async def test_microphone_mute_immediately_stops_asr_and_discards_pending_transcript(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    audio_sent = asyncio.Event()
    asr_stopped = asyncio.Event()

    class FakeTrack:
        def __init__(self) -> None:
            self.frames = 0

        async def recv(self) -> object:
            self.frames += 1
            if self.frames == 1:
                return object()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    class FakeResampler:
        def __init__(self, target_rate: int) -> None:
            self.target_rate = target_rate

        def resample_to_pcm16(self, _frame: object) -> bytes:
            return (12000).to_bytes(2, "little", signed=True) * 1600

    class FakeASR:
        def __init__(self, on_transcript) -> None:  # type: ignore[no-untyped-def]
            self.on_transcript = on_transcript

        async def start(self) -> None:
            pass

        async def send_pcm16(self, _pcm: bytes) -> None:
            self.on_transcript(Transcript("不应在静音后提交", False))
            audio_sent.set()

        async def stop(self) -> None:
            self.on_transcript(Transcript("不应在静音后提交", True))
            asr_stopped.set()

    def create_fake_asr(_settings, on_transcript, _on_error):  # type: ignore[no-untyped-def]
        return FakeASR(on_transcript)

    monkeypatch.setattr(session_module, "PCM16Resampler", FakeResampler)
    monkeypatch.setattr(session_module, "create_asr_session", create_fake_asr)
    settings = Settings(
        app_shared_secret="x" * 32,
        jwt_secret="y" * 32,
        auto_vad_min_speech_ms=0,
    )
    session = VoiceBridgeSession("test-session", settings)
    consumer = asyncio.create_task(session._consume_audio(FakeTrack()))
    await asyncio.wait_for(audio_sent.wait(), timeout=1)

    await session._handle_client_message(json.dumps({"type": "microphone_muted", "muted": True}))
    await asyncio.wait_for(asr_stopped.wait(), timeout=1)
    await asyncio.sleep(0)
    consumer.cancel()
    await asyncio.gather(consumer, return_exceptions=True)
    event_types = [event.type for event in session.events.history]
    await session.close()

    assert "transcript_discarded" in event_types
    assert "final_transcript" not in event_types
    assert session._respond_task is None


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
