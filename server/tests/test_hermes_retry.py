import httpx

from app.integrations.hermes import _retryable_error


def test_retryable_hermes_transport_and_status_errors() -> None:
    request = httpx.Request("POST", "http://hermes/v1/chat/completions")

    assert _retryable_error(httpx.ConnectError("offline", request=request)) is True
    assert _retryable_error(httpx.ReadTimeout("slow", request=request)) is True
    for status_code in (429, 502, 503, 504):
        response = httpx.Response(status_code, request=request)
        assert _retryable_error(httpx.HTTPStatusError("failed", request=request, response=response)) is True


def test_non_retryable_hermes_errors() -> None:
    request = httpx.Request("POST", "http://hermes/v1/chat/completions")
    response = httpx.Response(400, request=request)

    assert _retryable_error(httpx.HTTPStatusError("failed", request=request, response=response)) is False
    assert _retryable_error(RuntimeError("bad response")) is False
