#!/usr/bin/env python3
import asyncio
import os

from playwright.async_api import async_playwright


BASE_URL = os.environ.get("BASE_URL", "https://127.0.0.1:10005").rstrip("/")
SHARED_SECRET = os.environ.get("APP_SHARED_SECRET", "")

POOR_STATS = """(() => {
  const original = RTCPeerConnection.prototype.getStats;
  let received = 1000;
  let lost = 0;
  RTCPeerConnection.prototype.getStats = async function (...args) {
    if (this.connectionState !== 'connected') return original.apply(this, args);
    received += 90;
    lost += 10;
    return new Map([
      ['in', {id: 'in', type: 'inbound-rtp', kind: 'audio', packetsReceived: received,
        packetsLost: lost, jitter: 0.1, jitterBufferDelay: 5, jitterBufferEmittedCount: 100}],
      ['pair', {id: 'pair', type: 'candidate-pair', selected: true, state: 'succeeded',
        currentRoundTripTime: 0.7, localCandidateId: 'local', remoteCandidateId: 'remote'}],
      ['local', {id: 'local', type: 'local-candidate', candidateType: 'host'}],
      ['remote', {id: 'remote', type: 'remote-candidate', candidateType: 'host'}],
    ]);
  };
})();"""


async def main() -> None:
    if not SHARED_SECRET:
        raise SystemExit("Set APP_SHARED_SECRET before running this test")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(ignore_https_errors=True)
        await context.add_init_script(POOR_STATS)
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
        await page.click("#recordButton")
        await page.click("#newConversationButton")
        quality = page.locator("#networkQuality[data-quality='poor']")
        await quality.wait_for(timeout=20_000)
        for _ in range(40):
            if "source buffer 1.20 s" in (await quality.get_attribute("title") or ""):
                break
            await asyncio.sleep(0.25)
        else:
            raise AssertionError("adaptive source buffer did not reach 1.20 s")
        details = await page.locator("#networkQuality").get_attribute("title")
        print(f"quality=poor {details}")
        await page.evaluate("document.querySelector('#recordButton').click()")
        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
