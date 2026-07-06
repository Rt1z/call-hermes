import json

import httpx

from app.config import Settings
from app.integrations.hermes import (
    HermesClient,
    _extract_openai_event,
    _missing_assistant_messages,
    _turn_assistant_messages,
)


def test_extracts_stream_delta_and_finish_reason() -> None:
    text, finish_reason = _extract_openai_event(
        json.dumps({"choices": [{"delta": {"content": "完整回复"}, "finish_reason": "stop"}]})
    )

    assert text == "完整回复"
    assert finish_reason == "stop"


def test_extracts_non_stream_message_and_content_parts() -> None:
    text, finish_reason = _extract_openai_event(
        json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "第一句。"},
                                {"type": "text", "text": "第二句。"},
                            ]
                        },
                        "finish_reason": "length",
                    }
                ]
            }
        )
    )

    assert text == "第一句。第二句。"
    assert finish_reason == "length"


def test_extracts_current_turn_independent_assistant_messages() -> None:
    payload = {
        "data": [
            {"role": "user", "content": "older"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "current question"},
            {"role": "assistant", "content": "先说明第一点。"},
            {"role": "tool", "content": "tool result"},
            {"role": "assistant", "content": "再补充第二点。"},
        ]
    }

    assert _turn_assistant_messages(payload, "current question") == [
        "先说明第一点。",
        "再补充第二点。",
    ]


def test_finds_missing_and_partially_streamed_assistant_messages() -> None:
    messages = ["第一条完整消息。", "第二条完整消息。", "第三条完整消息。"]

    assert _missing_assistant_messages(messages, "第一条完整消息。第三条完整消息。") == [
        "第二条完整消息。"
    ]
    assert _missing_assistant_messages(messages[:2], "第一条完整消息。第二条") == ["完整消息。"]


async def test_stream_chat_recovers_unstreamed_assistant_message_from_transcript() -> None:
    events = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"第一条。"},"finish_reason":"stop"}]}',
            "",
            "data: [DONE]",
            "",
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                text=events,
                headers={
                    "Content-Type": "text/event-stream",
                    "X-Hermes-Session-Id": "session-1",
                },
            )
        assert request.url.path == "/api/sessions/session-1/messages"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"role": "user", "content": "test"},
                    {"role": "assistant", "content": "第一条。"},
                    {"role": "assistant", "content": "第二条。"},
                ]
            },
        )

    settings = Settings(
        app_shared_secret="x" * 32,
        jwt_secret="y" * 32,
        hermes_base_url="http://hermes.test",
    )
    chunks = [
        chunk
        async for chunk in HermesClient(settings, httpx.MockTransport(handler)).stream_chat("test")
    ]

    assert chunks == ["第一条。", "\n\n第二条。"]
