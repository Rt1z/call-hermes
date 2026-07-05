#!/usr/bin/env python3
import asyncio
import json
import os

from playwright.async_api import BrowserType, async_playwright


BASE_URL = os.environ.get("BASE_URL", "https://127.0.0.1:10005").rstrip("/")
SHARED_SECRET = os.environ.get("APP_SHARED_SECRET", "")


async def run_engine(name: str, engine: BrowserType) -> tuple[str, str]:
    browser = await engine.launch(headless=True)
    context = await browser.new_context(ignore_https_errors=True, service_workers="block")
    page = await context.new_page()

    init_script = """(() => {
            try {
                localStorage.setItem('hermes.sharedSecret', __SHARED_SECRET__);
                localStorage.setItem('hermes.debugMode', 'true');
            } catch (_) {
                // about:blank has no local storage; the script runs again on navigation.
            }
            if (!navigator.mediaDevices) return;
            navigator.mediaDevices.getUserMedia = async () => {
                const AudioContextClass = window.AudioContext || window.webkitAudioContext;
                const context = new AudioContextClass();
                const oscillator = context.createOscillator();
                const gain = context.createGain();
                const destination = context.createMediaStreamDestination();
                gain.gain.value = 0.001;
                oscillator.connect(gain).connect(destination);
                oscillator.start();
                window.__hermesTestAudio = { context, oscillator };
                return destination.stream;
            };
            navigator.mediaDevices.enumerateDevices = async () => [{
                deviceId: 'ci-microphone',
                groupId: 'ci',
                kind: 'audioinput',
                label: 'CI synthetic microphone',
                toJSON() { return this; },
            }];
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
    try:
        await page.locator("#status", has_text="Mic off").wait_for(timeout=20_000)
        result = "connected"
        await page.evaluate("document.querySelector('#recordButton').click()")
        await page.locator("#status", has_text="Ready").wait_for(timeout=5_000)
    except Exception:  # noqa: BLE001
        result = await page.locator("#status").inner_text()
    await browser.close()
    return name, result


async def main() -> None:
    if not SHARED_SECRET:
        raise SystemExit("Set APP_SHARED_SECRET before running this smoke test")
    async with async_playwright() as playwright:
        results = []
        for name, engine in (
            ("chromium", playwright.chromium),
            ("firefox", playwright.firefox),
            ("webkit", playwright.webkit),
        ):
            results.append(await run_engine(name, engine))
    failures = [result for result in results if result[1] != "connected"]
    print(results)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
