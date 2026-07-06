import asyncio
import base64
import logging
import math
import struct
from collections.abc import AsyncIterator
from typing import Protocol

import dashscope
from dashscope.audio.qwen_tts_realtime import QwenTtsRealtime, QwenTtsRealtimeCallback
from dashscope.audio.qwen_tts_realtime.qwen_tts_realtime import (
    AudioFormat as QwenRealtimeAudioFormat,
)
from dashscope.audio.tts_v2 import AudioFormat, ResultCallback, SpeechSynthesizer

from app.config import Settings
from app.circuit_breaker import tts_breaker

logger = logging.getLogger("call_hermes.tts")
TTSQueueItem = bytes | None | Exception


class TTSSession(Protocol):
    async def synthesize_stream(self, text_chunks: AsyncIterator[str]) -> AsyncIterator[bytes]: ...


class DashScopeTTSSession:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def synthesize_stream(self, text_chunks: AsyncIterator[str]) -> AsyncIterator[bytes]:
        tts_breaker.before_call()
        try:
            if self._settings.dashscope_tts_model.endswith("-realtime"):
                async for chunk in self._synthesize_qwen_realtime(text_chunks):
                    yield chunk
                tts_breaker.record_success()
                return

            dashscope.api_key = self._settings.dashscope_api_key
            queue: asyncio.Queue[TTSQueueItem] = asyncio.Queue()
            loop = asyncio.get_running_loop()
            callback = _TTSCallback(loop, queue)

            synthesizer = SpeechSynthesizer(
                model=self._settings.dashscope_tts_model,
                voice=self._settings.dashscope_tts_voice,
                format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                callback=callback,
            )

            async def feed() -> None:
                try:
                    async for chunk in text_chunks:
                        if chunk.strip():
                            await _control_call(self._settings, synthesizer.streaming_call, chunk)
                    await _control_call(self._settings, synthesizer.streaming_complete)
                except Exception as exc:
                    error = RuntimeError("TTS feed failed")
                    error.__cause__ = exc
                    loop.call_soon_threadsafe(queue.put_nowait, error)

            feeder = asyncio.create_task(feed())
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    if isinstance(item, Exception):
                        raise item
                    yield item
            finally:
                feeder.cancel()
            tts_breaker.record_success()
        except Exception:
            tts_breaker.record_failure()
            raise

    async def _synthesize_qwen_realtime(
        self, text_chunks: AsyncIterator[str]
    ) -> AsyncIterator[bytes]:
        dashscope.api_key = self._settings.dashscope_api_key
        queue: asyncio.Queue[TTSQueueItem] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        callback = _QwenRealtimeTTSCallback(loop, queue)
        synthesizer = QwenTtsRealtime(
            model=self._settings.dashscope_tts_model,
            callback=callback,
            url=self._settings.dashscope_tts_ws_url,
        )
        await _control_call(self._settings, synthesizer.connect)
        await _control_call(
            self._settings,
            synthesizer.update_session,
            voice=self._settings.dashscope_tts_voice,
            response_format=QwenRealtimeAudioFormat.PCM_24000HZ_MONO_16BIT,
            speech_rate=self._settings.dashscope_tts_speech_rate,
        )

        async def feed() -> None:
            try:
                async for chunk in text_chunks:
                    if chunk.strip():
                        await _control_call(self._settings, synthesizer.append_text, chunk)
                await _control_call(self._settings, synthesizer.commit)
                await _control_call(self._settings, synthesizer.finish)
            except Exception as exc:
                error = RuntimeError("TTS feed failed")
                error.__cause__ = exc
                loop.call_soon_threadsafe(queue.put_nowait, error)

        feeder = asyncio.create_task(feed())
        emitted_audio = False
        try:
            while True:
                try:
                    item = await asyncio.wait_for(
                        queue.get(),
                        timeout=self._settings.dashscope_tts_audio_timeout_seconds,
                    )
                except asyncio.TimeoutError as exc:
                    logger.error(
                        "qwen realtime tts timed out waiting for audio timeout_seconds=%.1f",
                        self._settings.dashscope_tts_audio_timeout_seconds,
                    )
                    raise RuntimeError("TTS timed out waiting for audio") from exc
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                emitted_audio = True
                yield item
            if not emitted_audio:
                raise RuntimeError("TTS returned no audio")
        finally:
            feeder.cancel()
            try:
                await _control_call(self._settings, synthesizer.close)
            except Exception:
                logger.warning("failed to close qwen realtime tts websocket", exc_info=True)


class _TTSCallback(ResultCallback):
    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[TTSQueueItem]) -> None:
        self._loop = loop
        self._queue = queue

    def on_data(self, data: bytes) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, data)

    def on_complete(self) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    def on_error(self, message) -> None:  # type: ignore[no-untyped-def]
        self._loop.call_soon_threadsafe(
            self._queue.put_nowait,
            RuntimeError(f"TTS error: {_error_message(message)}"),
        )


class _QwenRealtimeTTSCallback(QwenTtsRealtimeCallback):
    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[TTSQueueItem]) -> None:
        self._loop = loop
        self._queue = queue

    def on_open(self) -> None:
        return None

    def on_event(self, message: dict) -> None:
        event_type = message.get("type")
        if event_type == "response.audio.delta":
            delta = message.get("delta")
            if isinstance(delta, str) and delta:
                try:
                    audio = base64.b64decode(delta)
                except Exception:
                    audio = b""
                if audio:
                    self._loop.call_soon_threadsafe(self._queue.put_nowait, audio)
        elif event_type in {"response.done", "session.finished"}:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
        elif event_type == "error":
            self._loop.call_soon_threadsafe(
                self._queue.put_nowait,
                RuntimeError(f"TTS error: {_error_message(message)}"),
            )

    def on_close(self, close_status_code, close_msg) -> None:  # type: ignore[no-untyped-def]
        self._loop.call_soon_threadsafe(self._queue.put_nowait, None)


def _error_message(message) -> str:  # type: ignore[no-untyped-def]
    if isinstance(message, dict):
        for key in ("message", "error", "code"):
            value = message.get(key)
            if value:
                return str(value)
    return str(getattr(message, "message", message))


async def _control_call(settings: Settings, function, *args, **kwargs):  # type: ignore[no-untyped-def]
    return await asyncio.wait_for(
        asyncio.to_thread(function, *args, **kwargs),
        timeout=settings.dashscope_control_timeout_seconds,
    )


class MockTTSSession:
    async def synthesize_stream(self, text_chunks: AsyncIterator[str]) -> AsyncIterator[bytes]:
        consumed = ""
        async for chunk in text_chunks:
            consumed += chunk
        duration_seconds = max(0.6, min(4.0, len(consumed) / 16))
        sample_rate = 24000
        frame_count = int(sample_rate * duration_seconds)
        buf = bytearray()
        for i in range(frame_count):
            sample = int(0.12 * 32767 * math.sin(2 * math.pi * 440 * i / sample_rate))
            buf.extend(struct.pack("<h", sample))
            if len(buf) >= 2400:
                yield bytes(buf)
                buf.clear()
                await asyncio.sleep(0.05)
        if buf:
            yield bytes(buf)


def create_tts_session(settings: Settings) -> TTSSession:
    if settings.use_mock_tts:
        return MockTTSSession()
    return DashScopeTTSSession(settings)
