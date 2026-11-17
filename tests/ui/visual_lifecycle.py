"""THE full human-flow lifecycle test on a real in-scope target.

start (model + effort + prompt) -> watch tools render -> steer ->
pause -> stop -> resume -> delete. Screenshot at every hop; the human
(me) looks at every frame afterwards.
"""
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

SHOTS = Path("/Users/williamjiang/suijin/tests/ui/shots")
SHOTS.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8790"
PROMPT = "Check the security headers of www.design.webflow.com and summarize the findings"


def shot(page, name):
    page.screenshot(path=str(SHOTS / f"{name}.png"))
    print(f"  [shot] {name}")


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_context(locale="en-US", viewport={"width": 1440, "height": 900}).new_page()
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        # ── 1. hero: type the engagement ──
        inp = page.locator('input[aria-label="What should we hack today?"]')
        inp.fill(PROMPT)
        shot(page, "50-typed")

        # ── 2. model picker (before sending — lands on the session after create) ──
        model_btn = page.get_by_role("button", name="Model")
        if model_btn.count():
            model_btn.first.click()
            page.wait_for_timeout(1200)
            shot(page, "51-model-menu")
            # close it — pick after the session exists (this is the landing)
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)

        # ── 3. send ──
        inp.press("Enter")
        page.wait_for_timeout(8000)
        sid = page.url.split("/chats/")[-1]
        print(f"  session: {sid}")
        shot(page, "52-session-started")

        # ── 4. pick the model INSIDE the live session ──
        model_btn = page.get_by_role("button", name="Model")
        if model_btn.count():
            model_btn.first.click()
            page.wait_for_timeout(1500)
            shot(page, "53-model-options")
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)

        # ── 5. effort control (the circle next to Model) ──
        effort_btn = page.locator('button[aria-label*="easoning"], button[aria-label*="ffort"], button[aria-label*="hinking"]').first
        if effort_btn.count():
            effort_btn.click()
            page.wait_for_timeout(1000)
            shot(page, "54-effort-menu")
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        else:
            print("  no effort button found (may be capability-gated)")

        # ── 6. let the agents work, then watch tools render ──
        page.wait_for_timeout(20000)
        shot(page, "55-tools-streaming")

        # ── 7. steer mid-run like a human ──
        comp = page.get_by_placeholder("Ask Suijin")
        if comp.count():
            comp.fill("Focus only on the CSP header — is it missing or misconfigured?")
            comp.press("Enter")
            page.wait_for_timeout(6000)
            shot(page, "56-steered")
        print("phase1 done — pausing/stopping in the same page")

        # ── 8. STOP via the square button ──
        stop_btn = page.locator('button[aria-label*="top" i]').first
        clicked = False
        if stop_btn.count():
            stop_btn.click()
            clicked = True
        else:
            # the chat menu carries Stop too
            more = page.locator('button[aria-label="Chat menu"]').first
            if more.count():
                more.click()
                page.wait_for_timeout(600)
                item = page.get_by_text("Stop", exact=False).first
                if item.count():
                    item.click()
                    clicked = True
        print(f"  stop clicked: {clicked}")
        page.wait_for_timeout(4000)
        shot(page, "57-stopped")

        # ── 9. RESUME: send a fresh prompt into the stopped swarm ──
        comp = page.get_by_placeholder("Ask Suijin")
        if comp.count():
            comp.fill("Also check HSTS on the same host")
            comp.press("Enter")
            page.wait_for_timeout(8000)
            shot(page, "58-resumed")

        browser.close()
    print("DONE")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
