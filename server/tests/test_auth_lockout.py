import pytest
from fastapi import HTTPException, Request

from app.config import Settings
from app.rate_limit import clear_auth_failures, enforce_auth_rate_limit, record_auth_failure


def test_repeated_auth_failures_trigger_temporary_lockout() -> None:
    request = Request({"type": "http", "client": ("192.0.2.44", 1234), "headers": []})
    settings = Settings(
        app_shared_secret="x" * 32,
        jwt_secret="y" * 32,
        auth_lockout_failures=3,
        auth_rate_limit_requests=20,
    )
    clear_auth_failures(request)
    try:
        for _ in range(3):
            record_auth_failure(request)
        with pytest.raises(HTTPException) as exc_info:
            enforce_auth_rate_limit(request, settings)
        assert exc_info.value.status_code == 429
        assert "Retry-After" in (exc_info.value.headers or {})
    finally:
        clear_auth_failures(request)
