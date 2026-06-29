import pytest
from fastapi import HTTPException

from app.config import Settings
from app.main import OfferRequest, app, _settings_for_offer
from fastapi.testclient import TestClient


def _settings() -> Settings:
    return Settings(app_shared_secret="x" * 32, jwt_secret="y" * 32)


def test_offer_overrides_tts_voice_and_speech_rate() -> None:
    settings = _settings()
    offer = OfferRequest(
        sdp="v=0",
        tts_voice="Ryan",
        tts_speech_rate=1.25,
    )

    session_settings = _settings_for_offer(settings, offer)

    assert session_settings.dashscope_tts_voice == "Ryan"
    assert session_settings.dashscope_tts_speech_rate == 1.25
    assert settings.dashscope_tts_voice == "Cherry"


def test_offer_rejects_unknown_tts_voice() -> None:
    with pytest.raises(HTTPException):
        _settings_for_offer(_settings(), OfferRequest(sdp="v=0", tts_voice="Unknown"))


def test_offer_rejects_out_of_range_speech_rate() -> None:
    with pytest.raises(HTTPException):
        _settings_for_offer(_settings(), OfferRequest(sdp="v=0", tts_speech_rate=2.5))


def test_rtc_config_exposes_adaptive_buffer_settings() -> None:
    client = TestClient(app)
    settings = _settings()
    from app.main import get_settings

    app.dependency_overrides[get_settings] = lambda: settings
    try:
        auth = client.post(
            "/auth/session",
            json={"shared_secret": "x" * 32},
        ).json()
        response = client.get(
            "/rtc/config",
            headers={"Authorization": f"Bearer {auth['token']}"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["audio"] == {
        "adaptive_buffer_enabled": True,
        "prebuffer_seconds": 0.6,
        "prebuffer_min_seconds": 0.5,
        "prebuffer_max_seconds": 1.2,
    }
