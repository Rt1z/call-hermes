from collections import defaultdict
from threading import Lock


class RuntimeMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._observations: dict[str, dict[str, float]] = {}

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            observation = self._observations.setdefault(
                name,
                {"count": 0, "sum": 0.0, "max": value, "last": value},
            )
            observation["count"] += 1
            observation["sum"] += value
            observation["max"] = max(observation["max"], value)
            observation["last"] = value

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            observations = {
                name: {
                    "count": int(values["count"]),
                    "average": round(values["sum"] / values["count"], 2),
                    "total": round(values["sum"], 2),
                    "max": round(values["max"], 2),
                    "last": round(values["last"], 2),
                }
                for name, values in self._observations.items()
                if values["count"]
            }
            return {"counters": dict(self._counters), "observations": observations}


runtime_metrics = RuntimeMetrics()
