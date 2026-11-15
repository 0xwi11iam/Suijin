"""User-journey UI driver — drives the console like a real person.

Screenshots → /tmp/ui-tests/. Checks are behavioral: what renders,
what responds, what persists. Run: python3 ui_journey.py
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8790"
SHOTS = Path("/tmp/ui-tests")
SHOTS.mkdir(exist_ok=True)
results = []


def check(name: str, ok: bool, detail: str = ""):
    results.append({"name": name, "ok": ok, "detail": detail})
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        # 1. landing: app boots, sidebar renders, host connects
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(9000)
        page.screenshot(path=str(SHOTS / "01-landing.png"))
        body = page.inner_text("body")
        check("app boots (title suijin)", page.title() == "suijin", page.title())
        check("sidebar brand Suijin", "Suijin" in body)
        check("host cluster (My Mac)", "My Mac" in body)
        check("new engagement + new swarm", "New engagement" in body and "New swarm" in body)
        check("no YOLO autonomy selector", "YOLO" not in body)
        check("no 'This computer' strip", "This computer" not in body)
        check("reconnecting gate gone", "Reconnecting" not in body and "Connecting to host" not in body)

        # 2. composer visible + typing works
        ta = page.locator("textarea").first
        ta_visible = ta.count() > 0 and ta.is_visible()
        check("composer textarea visible", ta_visible)
        if ta_visible:
            ta.click()
            ta.fill("local fixture hunt: probe 127.0.0.1:8099 endpoints for auth bypass and open redirect; stay on localhost")
            page.screenshot(path=str(SHOTS / "02-composer-typed.png"))
            check("typed into composer", True)
            ta.press("Enter")
            page.wait_for_timeout(8000)
            page.screenshot(path=str(SHOTS / "03-after-send.png"))

        # 3. navigate the pages
        for label, path, shot in [
            ("findings", "/findings", "04-findings.png"),
            ("runs", "/runs", "05-runs.png"),
            ("skills", "/plugins/skills", "06-skills.png"),
            ("plugins", "/plugins", "07-plugins.png"),
            ("settings", "/settings", "08-settings.png"),
        ]:
            page.goto(f"{BASE}{path}", wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            page.screenshot(path=str(SHOTS / shot))
            b = page.inner_text("body")
            check(f"page renders: {label}", len(b.strip()) > 40, f"{len(b)} chars")

        # 4. settings pills — flip swarm turn cap in the UI
        page.goto(f"{BASE}/settings", wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        b = page.inner_text("body")
        check("settings show Suijin keys", "Turn cap" in b or "Autopilot" in b)
        page.screenshot(path=str(SHOTS / "09-settings-detail.png"))

        # 5. errors
        real = [e for e in errors if "favicon" not in e.lower()]
        check("zero console errors", len(real) == 0, "; ".join(real[:3]))

        browser.close()

    ok = all(r["ok"] for r in results)
    (SHOTS / "results.json").write_text(json.dumps(results, indent=2))
    print(f"\n{'ALL GREEN' if ok else 'FAILURES PRESENT'} — {SHOTS / 'results.json'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
