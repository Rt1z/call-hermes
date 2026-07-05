from app.auth import create_session_token, verify_bearer_identity, verify_bearer_token
from app.config import Settings


def test_session_token_round_trip() -> None:
    settings = Settings(app_shared_secret="x" * 32, jwt_secret="y" * 32)
    session = create_session_token(settings, "Test browser")
    assert verify_bearer_token(settings, f"Bearer {session['token']}") == session["session_id"]
    assert verify_bearer_identity(settings, f"Bearer {session['token']}").device_name == "Test browser"
