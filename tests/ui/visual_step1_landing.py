"""Visual test step 1: hero page renders, prompt starts an engagement.

The standing rule (operator directive 2026-11-17): every check is
screenshot -> interact -> screenshot -> LOOK. Assertions only find the
DOM; the after-screenshot is the proof.
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

SHOTS = Path(__file__).resolve().parent / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8790"


def shot(page, name: str) -> Path:
    p = SHOTS / f"{name}.png"
    page.screenshot(path=str(p), full_page=False)
    print(f"  shot: {p.name} ({p.stat().st_size // 1024}KB)")
    return p


def main() -> int:
    prompt = "Visual test engagement: report the page title of example.com"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="en-US", viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        # ── 1. land on the hero ─────────────────────────────────────
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        shot(page, "01-landing-hero")

        # the live landing uses the standard composer, not the legacy
        # aria-label input — target it by placeholder
        hero_input = page.get_by_placeholder("Ask Suijin")
        if hero_input.count() == 0:
            print("  hero composer not found — dumping visible text:")
            print("  |", page.locator("body").inner_text()[:400].replace("\n", " | ")[:400])
            return 1
        print("  hero composer: FOUND")

        # ── 2. type the prompt ──────────────────────────────────────
        hero_input.fill(prompt)
        page.wait_for_timeout(300)
        shot(page, "02-prompt-typed")
        if prompt not in (hero_input.input_value() or ""):
            print("  FAIL: typed text not in input")
            return 1
        print("  typed prompt: OK")

        # ── 3. submit (the composer sends on Enter, or the send button) ──
        hack_btn = page.get_by_role("button", name="Hack it")
        if hack_btn.count() > 0:
            hack_btn.click()
            print("  clicked: Hack it")
        else:
            hero_input.press("Enter")
            print("  pressed: Enter")

        # ── 4. wait for the session to take over the page ────────────
        page.wait_for_timeout(6000)
        shot(page, "03-after-hack-it")
        url = page.url
        print(f"  url after submit: {url}")
        body = page.locator("body").inner_text()[:600].replace("\n", " | ")
        print(f"  body: {body[:400]}")

        # ── 5. look at the sidebar ───────────────────────────────────
        page.wait_for_timeout(4000)
        shot(page, "04-sidebar-session")
        sidebar_text = page.locator("nav").first.inner_text() if page.locator("nav").count() else ""
        print(f"  sidebar mentions session: {'Visual test engagement' in sidebar_text}")

        browser.close()
        if errors:
            print("  console errors:", errors[:3])
        return 0


if __name__ == "__main__":
    sys.exit(main())
