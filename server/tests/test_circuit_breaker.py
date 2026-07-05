import pytest

from app.circuit_breaker import CircuitBreaker, CircuitOpenError


def test_circuit_breaker_opens_and_recovers() -> None:
    now = [100.0]
    breaker = CircuitBreaker("provider", failure_threshold=3, recovery_seconds=30, now=lambda: now[0])

    breaker.record_failure()
    breaker.record_failure()
    breaker.before_call()
    breaker.record_failure()

    with pytest.raises(CircuitOpenError):
        breaker.before_call()
    assert breaker.snapshot() == {"open": True, "failures": 3, "retry_after_seconds": 30.0}

    now[0] += 31
    breaker.before_call()
    breaker.record_success()
    breaker.before_call()
    assert breaker.snapshot() == {"open": False, "failures": 0, "retry_after_seconds": 0.0}
