import json

from app.integrations.hermes import _extract_openai_event


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
