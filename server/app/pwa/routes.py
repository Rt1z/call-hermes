import base64
import logging
import os
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel

from app.account_store import AccountStore
from app.auth import verify_bearer_identity
from app.config import MAX_TTS_SPEECH_RATE, MIN_TTS_SPEECH_RATE, TTS_VOICE_OPTIONS, Settings, get_settings
from app.conversation_store import ConversationStore
from app.metrics import runtime_metrics
from app.pwa.audio import transcode_to_wav_mono_16k_file, wav_duration_seconds
from app.pwa.service import voice_turn
from app.pwa.trace import TurnTrace

logger = logging.getLogger("call_hermes.pwa")


class TurnResponse(BaseModel):
    turn_id: str
    transcript: str
    answer: str
    audio_mime: str
    audio_base64: str
    timings: dict[str, int]


class TurnError(BaseModel):
    turn_id: str
    message: str


router = APIRouter(prefix="/pwa", tags=["pwa"])


@router.post("/turn", response_model=TurnResponse, deprecated=True)
async def pwa_turn(
    response: Response,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
    audio: UploadFile = File(...),
    conversation_id: str | None = Form(default=None),
    tts_voice: str | None = Form(default=None),
    tts_speech_rate: float | None = Form(default=None),
    hermes_model: str | None = Form(default=None),
    system_prompt: str | None = Form(default=None),
    max_tokens: int | None = Form(default=None),
    history_max_turns: int | None = Form(default=None),
    language: str | None = Form(default=None),
) -> TurnResponse:
    identity = verify_bearer_identity(settings, authorization)
    account_store = getattr(request.app.state, "account_store", None)
    if not isinstance(account_store, AccountStore) or (
        identity.device_id != "legacy"
        and not account_store.device_active(identity.user_id, identity.device_id)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Device access revoked")
    session_id = identity.session_id
    active_conversation_id = conversation_id or session_id
    store = getattr(request.app.state, "conversation_store", None)
    if not isinstance(store, ConversationStore):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Conversation store unavailable")
    turn_settings = _turn_settings(
        settings,
        tts_voice,
        tts_speech_rate,
        hermes_model,
        system_prompt,
        max_tokens,
        history_max_turns,
        language,
    )
    history = store.load(active_conversation_id, identity.user_id)
    response.headers["X-Call-Hermes-Mode"] = "fallback-pwa-turn"
    response.headers["Deprecation"] = "true"
    turn_id = uuid4().hex[:12]
    trace = TurnTrace(turn_id=turn_id, logger=logger)
    data = await audio.read(settings.pwa_max_upload_bytes + 1)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=TurnError(turn_id=turn_id, message="No audio was recorded.").model_dump(),
        )
    if len(data) > settings.pwa_max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=TurnError(
                turn_id=turn_id,
                message="Recording is too large. Please keep it under 60 seconds.",
            ).model_dump(),
        )

    suffix = Path(audio.filename or "").suffix
    try:
        with trace.stage("transcode"):
            wav_path = transcode_to_wav_mono_16k_file(data, suffix)
        try:
            logger.info(
                "turn_id=%s pwa turn start filename=%s content_type=%s bytes=%d wav=%s duration=%.2fs",
                turn_id,
                audio.filename,
                audio.content_type,
                len(data),
                wav_path,
                wav_duration_seconds(wav_path),
            )
            transcript, answer, wav = await voice_turn(turn_settings, wav_path, trace, history=history)
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                logger.warning("turn_id=%s failed to remove temp wav %s", turn_id, wav_path, exc_info=True)
    except Exception as exc:  # noqa: BLE001
        runtime_metrics.increment("fallback_turn_errors")
        message = friendly_error_message(exc)
        logger.exception("turn_id=%s pwa turn failed user_message=%s", turn_id, message)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=TurnError(turn_id=turn_id, message=message).model_dump(),
        ) from exc

    trace.timings["total_ms"] = trace.total_ms
    runtime_metrics.increment("fallback_turns_completed")
    for name in ("asr_ms", "hermes_ms", "tts_ms", "total_ms"):
        value = trace.timings.get(name)
        if value is not None:
            runtime_metrics.observe(f"fallback_{name}", value)
    history.extend(
        [
            {"role": "user", "content": transcript},
            {"role": "assistant", "content": answer},
        ]
    )
    store.save(active_conversation_id, _trim_history(history, settings), identity.user_id)
    logger.info(
        "turn_id=%s pwa turn complete transcript_len=%d answer_len=%d wav_bytes=%d timings=%s",
        turn_id,
        len(transcript),
        len(answer),
        len(wav),
        trace.timings,
    )
    return TurnResponse(
        turn_id=turn_id,
        transcript=transcript,
        answer=answer,
        audio_mime="audio/wav",
        audio_base64=base64.b64encode(wav).decode("ascii"),
        timings=trace.timings,
    )


def _turn_settings(
    settings: Settings,
    voice: str | None,
    speech_rate: float | None,
    hermes_model: str | None = None,
    system_prompt: str | None = None,
    max_tokens: int | None = None,
    history_max_turns: int | None = None,
    language: str | None = None,
) -> Settings:
    updates: dict[str, object] = {}
    if voice is not None:
        if voice not in TTS_VOICE_OPTIONS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported TTS voice")
        updates["dashscope_tts_voice"] = voice
    if speech_rate is not None:
        if not MIN_TTS_SPEECH_RATE <= speech_rate <= MAX_TTS_SPEECH_RATE:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported TTS speech rate")
        updates["dashscope_tts_speech_rate"] = speech_rate
    if hermes_model is not None:
        if not 1 <= len(hermes_model) <= 100:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported Hermes model")
        updates["hermes_model"] = hermes_model
    if system_prompt is not None:
        prompt = system_prompt.strip()[:4000]
        if language == "zh-CN":
            prompt += "\n请优先使用简体中文回答。"
        elif language == "en-US":
            prompt += "\nReply in English unless the user requests another language."
        updates["hermes_system_prompt"] = prompt
    if max_tokens is not None:
        if not 100 <= max_tokens <= 4096:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported max tokens")
        updates["hermes_max_tokens"] = max_tokens
    if history_max_turns is not None:
        if not 1 <= history_max_turns <= 100:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported history length")
        updates["hermes_history_max_turns"] = history_max_turns
    return settings.model_copy(update=updates) if updates else settings


def _trim_history(messages: list[dict[str, str]], settings: Settings) -> list[dict[str, str]]:
    result = [dict(message) for message in messages]
    while len(result) > settings.hermes_history_max_turns * 2:
        del result[:2]
    while len(result) > 2 and sum(len(message["content"]) for message in result) > settings.hermes_history_max_chars:
        del result[:2]
    return result


def friendly_error_message(exc: Exception) -> str:
    text = str(exc)
    if "Audio decode failed" in text:
        return "I could not read that recording. Please try recording again."
    if "empty transcript" in text or "ASR returned" in text:
        return "I could not hear speech clearly. Please try again."
    if "TTS" in text:
        return "Speech synthesis failed. Please try again."
    if "Hermes" in text:
        return "Hermes is temporarily unavailable."
    return "The voice turn failed. Please try again."
