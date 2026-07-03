from collections.abc import AsyncIterator
import json
import logging

import httpx

from app.config import Settings

logger = logging.getLogger("call_hermes.hermes")


class HermesClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

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
        event_count = 0
        content_chars = 0
        finish_reason: str | None = None
        received_done = False
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self._settings.hermes_base_url.rstrip('/')}/v1/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()
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
                        content_chars += len(text)
                        yield text
        log = logger.warning if finish_reason == "length" else logger.info
        log(
            "Hermes stream ended events=%d chars=%d finish_reason=%s done=%s",
            event_count,
            content_chars,
            finish_reason or "unknown",
            received_done,
        )


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
