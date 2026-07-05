import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

from app.config import Settings
from app.circuit_breaker import asr_breaker


@dataclass(frozen=True)
class Transcript:
    text: str
    is_final: bool


TranscriptCallback = Callable[[Transcript], None]
ErrorCallback = Callable[[str], None]


class ASRSession(Protocol):
    async def start(self) -> None: ...
    async def send_pcm16(self, pcm: bytes) -> None: ...
    async def stop(self) -> None: ...


class DashScopeASRSession:
    def __init__(
        self,
        settings: Settings,
        on_transcript: TranscriptCallback,
        on_error: ErrorCallback,
    ) -> None:
        self._settings = settings
        self._on_transcript = on_transcript
        self._on_error = on_error
        self._recognition: Recognition | None = None
        self._loop = asyncio.get_running_loop()

    async def start(self) -> None:
        asr_breaker.before_call()
        dashscope.api_key = self._settings.dashscope_api_key
        dashscope.base_websocket_api_url = self._settings.dashscope_asr_ws_url
        callback = _RecognitionCallback(self._loop, self._on_transcript, self._on_error)
        self._recognition = Recognition(
            model=self._settings.dashscope_asr_model,
            format="pcm",
            sample_rate=16000,
            semantic_punctuation_enabled=False,
            callback=callback,
        )
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._recognition.start),
                timeout=self._settings.dashscope_control_timeout_seconds,
            )
        except Exception:
            asr_breaker.record_failure()
            raise
        asr_breaker.record_success()

    async def send_pcm16(self, pcm: bytes) -> None:
        if self._recognition:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._recognition.send_audio_frame, pcm),
                    timeout=self._settings.dashscope_control_timeout_seconds,
                )
            except Exception:
                asr_breaker.record_failure()
                raise

    async def stop(self) -> None:
        if self._recognition:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._recognition.stop),
                    timeout=self._settings.dashscope_control_timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001
                if "has stopped" not in str(exc):
                    raise
            finally:
                self._recognition = None


class _RecognitionCallback(RecognitionCallback):
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_transcript: TranscriptCallback,
        on_error: ErrorCallback,
    ) -> None:
        self._loop = loop
        self._on_transcript = on_transcript
        self._on_error = on_error

    def on_event(self, result: RecognitionResult) -> None:
        sentence = result.get_sentence()
        text = sentence.get("text", "")
        if text:
            is_final = RecognitionResult.is_sentence_end(sentence)
            self._loop.call_soon_threadsafe(self._on_transcript, Transcript(text, is_final))

    def on_error(self, message) -> None:  # type: ignore[no-untyped-def]
        self._loop.call_soon_threadsafe(self._on_error, str(getattr(message, "message", message)))


class MockASRSession:
    def __init__(self, on_transcript: TranscriptCallback, _on_error: ErrorCallback) -> None:
        self._on_transcript = on_transcript
        self._bytes = 0
        self._sent = False

    async def start(self) -> None:
        return None

    async def send_pcm16(self, pcm: bytes) -> None:
        self._bytes += len(pcm)
        if not self._sent and self._bytes >= 16000 * 2:
            self._sent = True
            self._on_transcript(Transcript("你好 Hermes，请简单介绍一下你自己。", False))
            await asyncio.sleep(0.2)
            self._on_transcript(Transcript("你好 Hermes，请简单介绍一下你自己。", True))

    async def stop(self) -> None:
        return None


def create_asr_session(
    settings: Settings,
    on_transcript: TranscriptCallback,
    on_error: ErrorCallback,
) -> ASRSession:
    if settings.use_mock_asr:
        return MockASRSession(on_transcript, on_error)
    return DashScopeASRSession(settings, on_transcript, on_error)
