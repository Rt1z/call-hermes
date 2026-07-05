#!/usr/bin/env python3
import asyncio
import json
import os
import ssl
from urllib.request import urlopen

from playwright.async_api import async_playwright


BASE_URL = os.environ.get("BASE_URL", "https://127.0.0.1:10005").rstrip("/")
SHARED_SECRET = os.environ.get("APP_SHARED_SECRET", "")
CYCLES = int(os.environ.get("CYCLES", "20"))


def active_sessions() -> int:
    context = ssl._create_unverified_context()
    with urlopen(f"{BASE_URL}/metrics", context=context, timeout=10) as response:
        return int(json.load(response)["gauges"]["active_sessions"])


async def main() -> None:
    if not SHARED_SECRET:
        raise SystemExit("Set APP_SHARED_SECRET before running this stability test")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()

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
        for index in range(CYCLES):
            await page.click("#recordButton")
            selector = "#newConversationButton" if index % 5 == 0 else "#resumeConversationButton"
            await page.click(selector)
            await page.locator("#status", has_text="Mic off").wait_for(timeout=20_000)
            await page.evaluate("document.querySelector('#recordButton').click()")
            await page.locator("#status", has_text="Ready").wait_for(timeout=5_000)
            count = active_sessions()
            print(f"cycle={index + 1} active_sessions={count}", flush=True)
            if count != 0:
                raise RuntimeError(f"session leak after cycle {index + 1}")
        await context.close()
        await browser.close()
    print(f"completed={CYCLES} final_active_sessions={active_sessions()}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
