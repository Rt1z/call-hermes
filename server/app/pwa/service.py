import asyncio
import logging
import os
from collections.abc import AsyncIterator

import dashscope
from dashscope.audio.asr import Recognition

from app.config import Settings
from app.integrations.asr import Transcript, create_asr_session
from app.integrations.hermes import HermesClient
from app.integrations.tts import create_tts_session
from app.integrations.tts_normalize import normalize_for_tts
from app.pwa.audio import pcm16_to_wav
from app.pwa.trace import TurnTrace

logger = logging.getLogger("call_hermes.pwa.service")


async def transcribe_wav_file(settings: Settings, wav_path: str, trace: TurnTrace | None = None) -> str:
    if settings.use_mock_asr:
        return "你好 Hermes，请简单介绍一下你自己。"

    dashscope.api_key = settings.dashscope_api_key

    def run() -> str:
        recognition = Recognition(
            model=settings.dashscope_asr_model,
            format="wav",
            sample_rate=16000,
            callback=None,
        )
        result = recognition.call(file=wav_path)
        logger.info(
            "turn_id=%s dashscope recognition result status=%s code=%s request_id=%s",
            trace.turn_id if trace else "-",
            getattr(result, "status_code", None),
            getattr(result, "code", None),
            result.get_request_id() if hasattr(result, "get_request_id") else None,
        )
        sentence = result.get_sentence()
        if isinstance(sentence, list):
            text = "".join(str(item.get("text", "")) for item in sentence if isinstance(item, dict))
        elif isinstance(sentence, dict):
            text = str(sentence.get("text", ""))
        else:
            text = ""
        text = text.strip()
        if not text:
            message = getattr(result, "message", "") or "ASR returned empty transcript"
            raise RuntimeError(message)
        return text

    return await asyncio.to_thread(run)


async def transcribe_pcm16(settings: Settings, pcm16: bytes) -> str:
    if settings.use_mock_asr:
        return "你好 Hermes，请简单介绍一下你自己。"

    loop = asyncio.get_running_loop()
    final_text: asyncio.Future[str] = loop.create_future()
    last_text = ""

    def on_transcript(transcript: Transcript) -> None:
        nonlocal last_text
        last_text = transcript.text
        if transcript.is_final and not final_text.done():
            final_text.set_result(transcript.text)

    def on_error(message: str) -> None:
        if not final_text.done():
            final_text.set_exception(RuntimeError(message))

    asr = create_asr_session(settings, on_transcript, on_error)
    await asr.start()
    try:
        chunk_size = 3200
        for offset in range(0, len(pcm16), chunk_size):
            await asr.send_pcm16(pcm16[offset : offset + chunk_size])
            await asyncio.sleep(0.02)
        try:
            return await asyncio.wait_for(final_text, timeout=8)
        except asyncio.TimeoutError:
            if last_text:
                return last_text
            raise RuntimeError("ASR did not return any transcript before timeout") from None
    finally:
        await asr.stop()


async def ask_hermes(
    settings: Settings,
    user_text: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    chunks: list[str] = []
    try:
        async for chunk in HermesClient(settings).stream_chat(user_text, history=history):
            chunks.append(chunk)
    except Exception as exc:
        logger.exception("hermes chat failed")
        raise RuntimeError("Hermes chat failed") from exc
    answer = "".join(chunks).strip()
    return answer or "Hermes 没有返回内容。"


async def synthesize_wav(settings: Settings, text: str) -> bytes:
    spoken = normalize_for_tts(text)

    async def chunks() -> AsyncIterator[str]:
        for piece in _split_for_tts(spoken):
            yield piece

    pcm_chunks: list[bytes] = []
    async for pcm in create_tts_session(settings).synthesize_stream(chunks()):
        pcm_chunks.append(pcm)
    return pcm16_to_wav(b"".join(pcm_chunks), sample_rate=24000)


async def voice_turn(
    settings: Settings,
    wav_path: str,
    trace: TurnTrace,
    history: list[dict[str, str]] | None = None,
) -> tuple[str, str, bytes]:
    logger.info(
        "turn_id=%s voice turn asr start wav=%s bytes=%d",
        trace.turn_id,
        wav_path,
        os.path.getsize(wav_path),
    )
    with trace.stage("asr"):
        transcript = await transcribe_wav_file(settings, wav_path, trace)
    logger.info("turn_id=%s voice turn asr complete transcript_chars=%d", trace.turn_id, len(transcript))
    with trace.stage("hermes"):
        answer = await ask_hermes(settings, transcript, history=history)
    logger.info("turn_id=%s voice turn hermes complete answer_len=%d", trace.turn_id, len(answer))
    with trace.stage("tts"):
        audio_wav = await synthesize_wav(settings, answer)
    logger.info("turn_id=%s voice turn tts complete wav_bytes=%d", trace.turn_id, len(audio_wav))
    return transcript, answer, audio_wav


def _split_for_tts(text: str) -> list[str]:
    parts: list[str] = []
    buffer = ""
    for char in text:
        buffer += char
        if char in "。！？.!?\n" or len(buffer) >= 40:
            if buffer.strip():
                parts.append(buffer)
            buffer = ""
    if buffer.strip():
        parts.append(buffer)
    return parts
