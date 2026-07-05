#!/usr/bin/env python3
import asyncio
import os

from playwright.async_api import BrowserType, async_playwright


BASE_URL = os.environ.get("BASE_URL", "https://127.0.0.1:10005").rstrip("/")
SHARED_SECRET = os.environ.get("APP_SHARED_SECRET", "")


async def run_engine(name: str, engine: BrowserType) -> tuple[str, str]:
    browser = await engine.launch(headless=True)
    page = await browser.new_page(ignore_https_errors=True)

    async def use_host_candidates(route) -> None:  # type: ignore[no-untyped-def]
        response = await route.fetch()
        payload = await response.json()
        payload["ice_servers"] = []
        await route.fulfill(response=response, json=payload)

    await page.route("**/rtc/config", use_host_candidates)
    await page.goto(BASE_URL)
    await page.evaluate(
        """secret => {
            localStorage.setItem('hermes.sharedSecret', secret);
            localStorage.setItem('hermes.debugMode', 'true');
        }""",
        SHARED_SECRET,
    )
    await page.reload()
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
        results = await asyncio.gather(
            run_engine("chromium", playwright.chromium),
            run_engine("firefox", playwright.firefox),
            run_engine("webkit", playwright.webkit),
        )
    failures = [result for result in results if result[1] != "connected"]
    print(results)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
