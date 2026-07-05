import time
from collections import defaultdict, deque
from typing import Deque

from fastapi import HTTPException, Request, status

from app.config import Settings


_buckets: dict[tuple[str, str], Deque[float]] = defaultdict(deque)
_auth_failures: dict[str, Deque[float]] = defaultdict(deque)


def enforce_auth_rate_limit(request: Request, settings: Settings) -> None:
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    failures = _auth_failures[client]
    while failures and now - failures[0] > settings.auth_lockout_seconds:
        failures.popleft()
    if len(failures) >= settings.auth_lockout_failures:
        retry_after = max(1, int(settings.auth_lockout_seconds - (now - failures[0])))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Authentication temporarily locked after repeated failures.",
            headers={"Retry-After": str(retry_after)},
        )
    enforce_rate_limit(
        request,
        scope="auth",
        limit=settings.auth_rate_limit_requests,
        window=settings.auth_rate_limit_window_seconds,
        detail="Too many auth attempts. Please wait and try again.",
    )


def record_auth_failure(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    _auth_failures[client].append(time.monotonic())


def clear_auth_failures(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    _auth_failures.pop(client, None)


def enforce_client_log_rate_limit(request: Request, settings: Settings) -> None:
    enforce_rate_limit(
        request,
        scope="client-log",
        limit=settings.client_log_rate_limit_requests,
        window=settings.client_log_rate_limit_window_seconds,
        detail="Too many client log entries.",
    )


def enforce_rate_limit(request: Request, scope: str, limit: int, window: int, detail: str) -> None:
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    key = (scope, client)
    bucket = _buckets[key]
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
        )
    bucket.append(now)
    if len(_buckets) > 10_000:
        stale = [bucket_key for bucket_key, values in _buckets.items() if not values or now - values[-1] > window]
        for bucket_key in stale:
            _buckets.pop(bucket_key, None)
