import asyncio

import app.main as main_module
from app.config import Settings


async def test_hermes_health_cache_coalesces_concurrent_checks(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = 0

    class FakeHermesClient:
        def __init__(self, _settings: Settings) -> None:
            pass

        async def health(self) -> tuple[bool, str]:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return True, "ok"

    settings = Settings(
        app_shared_secret="x" * 32,
        jwt_secret="y" * 32,
        dependency_health_cache_seconds=5,
    )
    monkeypatch.setattr(main_module, "HermesClient", FakeHermesClient)
    main_module.hermes_health_cached = None
    main_module.hermes_health_expires_at = 0

    results = await asyncio.gather(*(main_module._cached_hermes_health(settings) for _ in range(10)))

    assert results == [(True, "ok")] * 10
    assert calls == 1
