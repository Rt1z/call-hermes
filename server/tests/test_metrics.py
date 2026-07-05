from app.metrics import RuntimeMetrics


def test_runtime_metrics_aggregates_counters_and_observations() -> None:
    metrics = RuntimeMetrics()

    metrics.increment("turns_completed")
    metrics.increment("turns_completed", 2)
    metrics.observe("tts_ttfa_ms", 120)
    metrics.observe("tts_ttfa_ms", 180)

    assert metrics.snapshot() == {
        "counters": {"turns_completed": 3},
        "observations": {
            "tts_ttfa_ms": {
                "count": 2,
                "average": 150.0,
                "total": 300.0,
                "max": 180,
                "last": 180,
            }
        },
    }
