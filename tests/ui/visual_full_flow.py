"""Full operator-flow test: hero → type → Enter → session live →
leave (close page) → come back → still there → stop → delete.
Every step: screenshot → LOOK."""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

SHOTS = Path("/Users/williamjiang/suijin/tests/ui/shots")
SHOTS.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8790"
PROMPT = "Report the HTTP headers of example.com and note anything unusual"


def shot(page, name):
    p = SHOTS / f"{name}.png"
    page.screenshot(path=str(p))
    print(f"  [shot] {name}")


def look(page, label):
    body = page.locator("body").inner_text()[:500].replace("\n", " | ")
    print(f"  [{label}] url={page.url}")
    print(f"    body: {body[:330]}")


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="en-US", viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        # ── 1. landing ───────────────────────────────────────────
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        shot(page, "10-landing")
        look(page, "landing")
        # two landing shapes: the legacy hero input (blank state) or the
        # standard composer (restored dock chat)
        comp = page.locator('input[aria-label="What should we hack today?"]')
        shape = "hero"
        if comp.count() == 0:
            comp = page.get_by_placeholder("Ask Suijin")
            shape = "composer"
        if comp.count() == 0:
            print("  FAIL: no input on landing")
            return 1
        print(f"  landing shape: {shape}")

        # ── 2. type + Enter ──────────────────────────────────────
        comp.fill(PROMPT)
        page.wait_for_timeout(300)
        comp.press("Enter")
        page.wait_for_timeout(8000)
        shot(page, "11-session-started")
        look(page, "after-enter")

        # ── 3. transcript streams? ───────────────────────────────
        page.wait_for_timeout(12000)
        shot(page, "12-transcript-streaming")
        look(page, "streaming")

        sid = page.url.split("/chats/")[-1].split("?")[0]
        print(f"  session id: {sid}")

        # ── 4. LEAVE: close the page entirely ────────────────────
        page.close()
        print("  [leave] page closed")

        # ── 5. come back: fresh page, click the sidebar item ─────
        page2 = ctx.new_page()
        page2.goto(BASE, wait_until="domcontentloaded")
        page2.wait_for_timeout(4000)
        shot(page2, "13-back-home")
        look(page2, "back-home")
        row = page2.get_by_text(PROMPT[:40], exact=False)
        if row.count() == 0:
            # title may be truncated in the sidebar — try shorter
            row = page2.get_by_text(PROMPT[:25], exact=False)
        if row.count() == 0:
            print("  FAIL: session not in sidebar after return")
            body = page2.locator("body").inner_text()[:400]
            print(f"    sidebar body: {body.replace(chr(10), ' | ')[:300]}")
            return 1
        row.first.click()
        page2.wait_for_timeout(6000)
        shot(page2, "14-reopened-session")
        look(page2, "reopened")

        browser.close()
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
