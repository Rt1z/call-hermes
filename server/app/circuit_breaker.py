import time
from collections.abc import Callable
from threading import Lock


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_seconds: float = 30,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._now = now
        self._failures = 0
        self._open_until = 0.0
        self._lock = Lock()

    def before_call(self) -> None:
        with self._lock:
            if self._open_until > self._now():
                remaining = self._open_until - self._now()
                raise CircuitOpenError(
                    f"{self.name} is temporarily unavailable; retry in {remaining:.1f}s"
                )

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = 0.0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._open_until = self._now() + self.recovery_seconds

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            remaining = max(0.0, self._open_until - self._now())
            return {
                "open": remaining > 0,
                "failures": self._failures,
                "retry_after_seconds": round(remaining, 1),
            }


hermes_breaker = CircuitBreaker("Hermes")
asr_breaker = CircuitBreaker("DashScope ASR")
tts_breaker = CircuitBreaker("DashScope TTS")
