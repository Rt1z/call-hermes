import pytest
from fastapi import HTTPException

from app.config import Settings
from app.pwa.routes import _trim_history, _turn_settings


def _settings(**updates: object) -> Settings:
    return Settings(app_shared_secret="x" * 32, jwt_secret="y" * 32, **updates)


def test_fallback_turn_settings_apply_voice_and_rate() -> None:
    settings = _turn_settings(_settings(), "Ryan", 1.2)

    assert settings.dashscope_tts_voice == "Ryan"
    assert settings.dashscope_tts_speech_rate == 1.2


@pytest.mark.parametrize("voice,rate", [("Unknown", 1.0), ("Cherry", 3.0)])
def test_fallback_turn_settings_reject_invalid_values(voice: str, rate: float) -> None:
    with pytest.raises(HTTPException):
        _turn_settings(_settings(), voice, rate)


def test_fallback_history_trims_complete_turns() -> None:
    messages = [
        {"role": role, "content": content}
        for content in ("one", "two", "three")
        for role in ("user", "assistant")
    ]

    assert _trim_history(messages, _settings(hermes_history_max_turns=2)) == messages[-4:]
