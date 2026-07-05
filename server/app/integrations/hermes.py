from collections.abc import AsyncIterator
import asyncio
import json
import logging
from urllib.parse import quote

import httpx

from app.config import Settings
from app.circuit_breaker import hermes_breaker

logger = logging.getLogger("call_hermes.hermes")


class HermesClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def health(self) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{self._settings.hermes_base_url.rstrip('/')}/health")
            if response.status_code < 500:
                return True, f"HTTP {response.status_code}"
            return False, f"HTTP {response.status_code}"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    async def stream_chat(
        self,
        user_text: str,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        headers = {"Accept": "text/event-stream"}
        if self._settings.hermes_api_key:
            headers["Authorization"] = f"Bearer {self._settings.hermes_api_key}"

        payload = {
            "model": self._settings.hermes_model,
            "messages": _build_chat_messages(self._settings, user_text, history or []),
            "stream": True,
            "max_tokens": self._settings.hermes_max_tokens,
        }

        timeout = httpx.Timeout(self._settings.hermes_timeout_seconds)
        hermes_breaker.before_call()
        try:
            for attempt in range(1, self._settings.hermes_max_attempts + 1):
                event_count = 0
                content_chars = 0
                finish_reason: str | None = None
                received_done = False
                emitted_content = False
                streamed_content = ""
                try:
                    async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                        async with client.stream(
                            "POST",
                            f"{self._settings.hermes_base_url.rstrip('/')}/v1/chat/completions",
                            headers=headers,
                            json=payload,
                        ) as response:
                            response.raise_for_status()
                            hermes_session_id = response.headers.get("X-Hermes-Session-Id", "").strip()
                            async for line in response.aiter_lines():
                                if not line or not line.startswith("data: "):
                                    continue
                                data = line.removeprefix("data: ").strip()
                                if data == "[DONE]":
                                    received_done = True
                                    break
                                event_count += 1
                                text, event_finish_reason = _extract_openai_event(data)
                                if event_finish_reason:
                                    finish_reason = event_finish_reason
                                if text:
                                    emitted_content = True
                                    content_chars += len(text)
                                    streamed_content += text
                                    yield text
                            if hermes_session_id:
                                recovered = await _recover_turn_messages(
                                    client,
                                    self._settings,
                                    hermes_session_id,
                                    user_text,
                                    streamed_text=streamed_content,
                                )
                                for text in recovered:
                                    if streamed_content:
                                        text = f"\n\n{text}"
                                    content_chars += len(text)
                                    streamed_content += text
                                    yield text
                    log = logger.warning if finish_reason == "length" else logger.info
                    log(
                        "Hermes stream ended events=%d chars=%d finish_reason=%s done=%s attempt=%d",
                        event_count,
                        content_chars,
                        finish_reason or "unknown",
                        received_done,
                        attempt,
                    )
                    hermes_breaker.record_success()
                    return
                except Exception as exc:
                    can_retry = (
                        not emitted_content
                        and attempt < self._settings.hermes_max_attempts
                        and _retryable_error(exc)
                    )
                    if not can_retry:
                        raise
                    delay = self._settings.hermes_retry_backoff_seconds * attempt
                    logger.warning(
                        "Hermes stream attempt failed before first token attempt=%d retry_in=%.2fs error=%s",
                        attempt,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
        except Exception:
            hermes_breaker.record_failure()
            raise


def _build_chat_messages(
    settings: Settings,
    user_text: str,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if settings.hermes_system_prompt:
        messages.append({"role": "system", "content": settings.hermes_system_prompt})
    for message in history:
        role = message.get("role")
        content = message.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_text})
    return messages


def _extract_openai_delta(data: str) -> str:
    return _extract_openai_event(data)[0]


def _extract_openai_event(data: str) -> tuple[str, str | None]:

    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return "", None
    choices = payload.get("choices") or []
    if not choices:
        return "", None
    choice = choices[0]
    delta = choice.get("delta") or choice.get("message") or {}
    content = _content_text(delta.get("content"))
    finish_reason = choice.get("finish_reason")
    return content, finish_reason if isinstance(finish_reason, str) else None


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


async def _recover_turn_messages(
    client: httpx.AsyncClient,
    settings: Settings,
    session_id: str,
    user_text: str,
    *,
    streamed_text: str,
) -> list[str]:
    try:
        response = await client.get(
            f"{settings.hermes_base_url.rstrip('/')}/api/sessions/{quote(session_id, safe='')}/messages",
            headers=_auth_headers(settings),
        )
        response.raise_for_status()
        messages = _turn_assistant_messages(response.json(), user_text)
    except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning(
            "Hermes transcript reconciliation unavailable session_id=%s error=%s",
            session_id,
            exc,
        )
        return []
    missing = _missing_assistant_messages(messages, streamed_text)
    logger.info(
        "Hermes transcript reconciled session_id=%s assistant_messages=%d recovered=%d",
        session_id,
        len(messages),
        len(missing),
    )
    return missing


def _auth_headers(settings: Settings) -> dict[str, str]:
    if not settings.hermes_api_key:
        return {}
    return {"Authorization": f"Bearer {settings.hermes_api_key}"}


def _turn_assistant_messages(payload: object, user_text: str) -> list[str]:
    if not isinstance(payload, dict):
        return []
    raw_messages = payload.get("data")
    if not isinstance(raw_messages, list):
        return []
    start = -1
    fallback_start = -1
    for index, message in enumerate(raw_messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        fallback_start = index
        if _content_text(message.get("content")).strip() == user_text.strip():
            start = index
    if start < 0:
        start = fallback_start
    if start < 0:
        return []
    assistant_messages: list[str] = []
    for message in raw_messages[start + 1 :]:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user":
            break
        if message.get("role") != "assistant":
            continue
        content = _content_text(message.get("content")).strip()
        if content:
            assistant_messages.append(content)
    return assistant_messages


def _missing_assistant_messages(messages: list[str], streamed_text: str) -> list[str]:
    cursor = 0
    missing: list[str] = []
    for message in messages:
        if streamed_text.startswith(message, cursor):
            cursor += len(message)
            continue
        later = streamed_text.find(message, cursor)
        if later >= 0:
            cursor = later + len(message)
            continue
        available = streamed_text[cursor:]
        overlap = 0
        for left, right in zip(message, available, strict=False):
            if left != right:
                break
            overlap += 1
        if overlap == len(available) and overlap < len(message):
            cursor = len(streamed_text)
            message = message[overlap:]
        if message.strip():
            missing.append(message)
    return missing


def _retryable_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 502, 503, 504}
    return False
