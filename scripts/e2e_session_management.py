#!/usr/bin/env python3
import asyncio
import os

from playwright.async_api import async_playwright


BASE_URL = os.environ.get("BASE_URL", "https://127.0.0.1:10005").rstrip("/")
SHARED_SECRET = os.environ.get("APP_SHARED_SECRET", "")


async def connect(page) -> None:  # type: ignore[no-untyped-def]
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
    await page.locator("#status", has_text="Mic off").wait_for(timeout=25_000)


async def main() -> None:
    if not SHARED_SECRET:
        raise SystemExit("Set APP_SHARED_SECRET before running this test")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        first = await browser.new_page(ignore_https_errors=True)
        second = await browser.new_page(ignore_https_errors=True)
        try:
            await connect(first)
            await connect(second)
            await first.click("#settingsButton")
            await first.click("#activeSessionsButton")
            session_rows = first.locator("#activeSessionList .active-session-item")
            await session_rows.nth(1).wait_for()
            other = session_rows.filter(has_not_text="This device")
            first.once("dialog", lambda dialog: asyncio.create_task(dialog.accept()))
            await other.locator("button").click()
            await second.locator("#status", has_text="Session ended").wait_for(timeout=5_000)
            await second.locator("body:not(.calling)").wait_for(timeout=5_000)
            await session_rows.nth(1).wait_for(state="detached", timeout=5_000)
            await asyncio.sleep(6)
            assert await second.locator("#status").inner_text() == "Session ended"
            print("session management passed: remote termination did not reconnect")
        finally:
            if not first.is_closed():
                if await first.locator("body.calling").count():
                    async with first.expect_response(
                        lambda response: response.request.method == "DELETE"
                        and response.url.endswith("/rtc/session"),
                        timeout=3_000,
                    ):
                        await first.evaluate("document.querySelector('#recordButton').click()")
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
