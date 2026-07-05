#!/usr/bin/env python3
import asyncio
import json
import os

from playwright.async_api import BrowserType, async_playwright


BASE_URL = os.environ.get("BASE_URL", "https://127.0.0.1:10005").rstrip("/")
SHARED_SECRET = os.environ.get("APP_SHARED_SECRET", "")
SELECTED_BROWSER = os.environ.get("BROWSER", "").strip().lower()


async def run_engine(name: str, engine: BrowserType) -> tuple[str, str]:
    launch_options: dict[str, object] = {"headless": True}
    if name == "firefox":
        launch_options["firefox_user_prefs"] = {
            "media.peerconnection.ice.loopback": True,
            "media.peerconnection.ice.obfuscate_host_addresses": False,
        }
    browser = await engine.launch(**launch_options)
    try:
        context = await browser.new_context(ignore_https_errors=True, service_workers="block")
        page = await context.new_page()

        init_script = """(() => {
            try {
                localStorage.setItem('hermes.sharedSecret', __SHARED_SECRET__);
                localStorage.setItem('hermes.debugMode', 'true');
            } catch (_) {
                // about:blank has no local storage; the script runs again on navigation.
            }
        })()""".replace("__SHARED_SECRET__", json.dumps(SHARED_SECRET))
        await page.add_init_script(init_script)

        async def use_host_candidates(route) -> None:  # type: ignore[no-untyped-def]
            response = await route.fetch()
            payload = await response.json()
            payload["ice_servers"] = []
            await route.fulfill(response=response, json=payload)

        await page.route("**/rtc/config", use_host_candidates)
        await page.goto(BASE_URL)
        await page.click("#recordButton")
        await page.click("#newConversationButton")
        await page.locator("#status", has_text="Mic off").wait_for(timeout=45_000)
        await page.evaluate("document.querySelector('#recordButton').click()")
        await page.locator("#status", has_text="Ready").wait_for(timeout=5_000)
        return name, "connected"
    except Exception as error:  # noqa: BLE001
        detail = f"{type(error).__name__}: {error}".replace("\n", " ")
        try:
            status = await page.locator("#status").inner_text(timeout=1_000)
            detail = f"status={status!r}; {detail}"
        except Exception:  # noqa: BLE001
            pass
        print(f"::error title={name} WebRTC smoke failed::{detail}")
        return name, detail
    finally:
        await browser.close()


async def main() -> None:
    if not SHARED_SECRET:
        raise SystemExit("Set APP_SHARED_SECRET before running this smoke test")
    async with async_playwright() as playwright:
        engines = {
            "chromium": playwright.chromium,
            "firefox": playwright.firefox,
            "webkit": playwright.webkit,
        }
        if SELECTED_BROWSER and SELECTED_BROWSER not in engines:
            raise SystemExit(f"Unsupported BROWSER: {SELECTED_BROWSER}")
        selected = [SELECTED_BROWSER] if SELECTED_BROWSER else list(engines)
        results = []
        for name in selected:
            results.append(await run_engine(name, engines[name]))
    failures = [result for result in results if result[1] != "connected"]
    print(results)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
