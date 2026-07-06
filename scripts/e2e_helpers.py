import asyncio
import os


TEST_PASSWORD = os.environ.get(
    "E2E_TEST_PASSWORD", os.environ.get("APP_SHARED_SECRET", "")
)


async def start_call(page, conversation_button: str = "#newConversationButton") -> None:  # type: ignore[no-untyped-def]
    await page.click("#recordButton")
    button = page.locator(conversation_button)
    settings = page.locator("#settingsDialog")
    for _ in range(100):
        if await button.is_visible():
            await button.click()
            return
        if await settings.evaluate("dialog => dialog.open"):
            raise RuntimeError(
                "Authentication failed and opened Settings; set E2E_TEST_PASSWORD to the "
                "current account password"
            )
        await asyncio.sleep(0.05)
    raise TimeoutError("Conversation choice did not open after starting the call")


async def stop_call(page) -> None:  # type: ignore[no-untyped-def]
    async with page.expect_response(
        lambda response: (
            response.request.method == "DELETE"
            and response.url.endswith("/rtc/session")
        ),
        timeout=5_000,
    ):
        await page.evaluate("document.querySelector('#recordButton').click()")
    await page.locator("#status", has_text="Ready").wait_for(timeout=5_000)
