#!/usr/bin/env python3
"""Landing → chat, clicked through in a real browser.

Run against a console that is already up (default http://127.0.0.1:8790, override with
SUIJIN_WEB_URL):

    python3 tests/hero_journey.py

Everything that creates or changes state is intercepted at the network layer — session creation,
the inbox upload, the first message, the runtime PATCH — so this proves what the landing hands the
console and how the console reacts, without starting an engagement, touching a provider, or
leaving anything behind. Read-only endpoints (the model list, the runtime, the session list) are
real.

Exit status is non-zero if any check fails. Screenshots land under the temp dir, in
``suijin-hero-shots/``.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

BASE = os.environ.get("SUIJIN_WEB_URL", "http://127.0.0.1:8790").rstrip("/")
SHOTS = os.path.join(tempfile.gettempdir(), "suijin-hero-shots")
# A documentation-only name: this session only ever exists inside the stubbed network.
STUB = "qa-landing-journey"
SCOPE_TEXT = "# Scope\n\nexample.test only. Read-only.\n"

results: list[tuple[bool, str]] = []


def check(name: str, ok: object, detail: object = "") -> None:
    results.append((bool(ok), name))
    print(("PASS " if ok else "FAIL ") + name + (f"  — {str(detail)[:200]}" if not ok else ""), flush=True)


def main() -> int:
    os.makedirs(SHOTS, exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    captured: dict[str, object] = {}
    logs: list[tuple[str, str]] = []
    session = {"id": STUB, "target": "", "role": "audit", "autonomyMode": "yolo", "scopeConfigured": False,
               "localScopeConfigured": False, "status": "working", "createdAt": now, "updatedAt": now, "title": "stub"}
    runtime = {"providerId": "numes", "providerLabel": "Numes", "model": "deepseek/deepseek-v4.1-flash:free",
               "configured": True, "connectionIdentity": None, "diagnostics": {"valid": True, "reason": None, "message": None},
               "agentModels": {}, "singleModel": True, "autoRoute": False, "contextWindowTokens": None}

    def snapshot(text: str) -> dict:
        return {"session": session, "title": session["title"], "cursor": 1 if text else 0,
                "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}] if text else [],
                "events": [{"schemaVersion": 1, "sessionId": STUB, "sequence": 1, "occurredAt": now, "type": "user", "text": text}] if text else [],
                "pendingDecisions": [], "workers": [], "queuedMessages": [], "runtime": runtime, "scope": None,
                "scopeEnforcement": {"pluginId": "", "enabled": False, "projectPath": "", "message": ""},
                "usage": {"inputTokens": 0, "outputTokens": 0}, "lastOutcome": None, "compaction": None, "harness": None,
                "objective": "", "todos": None, "tools": []}

    def stub(route):
        request = route.request
        path = request.url.replace(BASE, "").split("?")[0]
        method = request.method
        base = f"/api/console/sessions/{STUB}"
        json_response = lambda body, status=200: route.fulfill(status=status, content_type="application/json", body=json.dumps(body))  # noqa: E731
        if method == "POST" and path == "/api/console/sessions":
            captured["create"] = request.post_data_json
            session["title"] = captured["create"].get("title", "stub")
            if captured.get("fail_create"):
                return json_response({"error": "stubbed failure"}, status=500)
            return json_response({"session": session})
        if method == "GET" and path == "/api/console/sessions":
            return json_response({"sessions": [session] if "create" in captured else []})
        if method == "POST" and path == base + "/files":
            captured["files"] = request.post_data_json
            return json_response({"ok": True, "session": session})
        if method == "POST" and path == base + "/messages":
            captured["message"] = request.post_data_json
            captured["sent"] = captured["message"].get("text", "")
            return json_response({"ok": True})
        if method == "PATCH" and path == "/api/console/runtime":
            captured["runtimePatch"] = request.post_data_json
            picked = captured["runtimePatch"]
            return json_response({"ok": True, "runtime": {"runtime": {"providerId": picked["providerId"], "model": picked["model"]}, "effort": "high"}})
        if method == "GET" and path == base:
            return json_response({"snapshot": snapshot(captured.get("sent", ""))})
        if method == "GET" and path == base + "/events":
            return json_response({"events": [], "cursor": 1, "gap": False})
        if path.startswith(base):
            return json_response({})
        if method != "GET":
            # Anything else that would change state is a journey bug: record it, do not send it.
            captured.setdefault("unexpected", []).append(f"{method} {path}")
            return json_response({"ok": True})
        return route.fallback()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
        page = context.new_page()
        page.on("console", lambda message: logs.append((message.type, message.text[:220])))
        page.on("pageerror", lambda error: logs.append(("pageerror", str(error)[:220])))
        page.on("requestfailed", lambda request: logs.append(("requestfailed", f"{request.method} {request.url[-70:]} {request.failure}")))
        page.route("**/api/**", stub)

        def hero():
            return page.frame_locator("iframe[data-suijin-hero-frame]")

        def composer():
            return hero().locator("[data-suijin-home-textarea]")

        def landing_frame():
            return next(frame for frame in page.frames if "zero/home.html" in frame.url)

        def popup():
            return landing_frame().locator('[data-slot="dropdown-menu-content"]').first

        def options():
            return [label.split("\n")[0] for label in popup().locator("[data-slot=dropdown-menu-item]").all_inner_texts()]

        page.goto(f"{BASE}/chats", wait_until="networkidle")
        composer().wait_for(timeout=20000)
        page.wait_for_timeout(800)

        # A — typing reaches the composer without a click
        page.keyboard.type("Ping from the landing journey")
        check("typing reaches the composer without a click", composer().input_value() == "Ping from the landing journey")
        check("send enables once there is a prompt", hero().locator("[data-suijin-home-submit]").is_enabled())
        page.keyboard.press("Shift+Enter")
        page.keyboard.type("second line")
        check("Shift+Enter inserts a newline and does not submit", "\n" in composer().input_value() and page.url.rstrip("/").endswith("/chats"))

        # B — Mode (0's context row)
        hero().locator('[aria-label="Mode"]').click()
        page.wait_for_timeout(400)
        check("Mode menu lists BBP / VDP / CTF / Pentest", options() == ["BBP", "VDP", "CTF", "Pentest"], options())
        page.screenshot(path=os.path.join(SHOTS, "01-mode-menu.png"))
        popup().get_by_text("Pentest", exact=True).click()
        page.wait_for_timeout(250)
        check("Pentest can be selected", hero().locator('[aria-label="Mode"]').inner_text().strip().startswith("Pentest"))
        check("Pentest says it needs a scope document", landing_frame().get_by_text("Pentest needs a scope document").count() == 1)
        check("send is blocked without the scope document", hero().locator("[data-suijin-home-submit]").is_disabled())

        # C — Permissions (0's autonomy slot)
        hero().locator("[data-suijin-home-permissions]").click()
        page.wait_for_timeout(400)
        check("Permissions menu lists Recon / Exploitation / Maximum", options() == ["Recon", "Exploitation", "Maximum"], options())
        page.screenshot(path=os.path.join(SHOTS, "02-permissions-menu.png"))
        popup().get_by_text("Recon", exact=True).click()
        page.wait_for_timeout(250)
        check("Recon can be selected", hero().locator("[data-suijin-home-permissions]").inner_text().strip() == "Recon")
        page.keyboard.type(" continues")
        check("typing continues in the composer after a menu choice", composer().input_value().endswith(" continues"), composer().input_value())

        # D — attachments
        hero().locator("[data-suijin-home-file-input]").set_input_files([
            {"name": "scope.md", "mimeType": "text/markdown", "buffer": SCOPE_TEXT.encode()},
            {"name": "logo.png", "mimeType": "image/png", "buffer": b"\x89PNG\r\n\x1a\n" + b"\0" * 40},
        ])
        page.wait_for_timeout(300)
        chips = hero().locator('[aria-label="Attached files"] span.truncate').all_inner_texts()
        check("a text file attaches and a binary is skipped", chips == ["scope.md"], chips)
        check("the skip is explained", "skipped" in hero().locator("[data-suijin-home-notice]").inner_text())
        check("the scope document clears the Pentest gate", hero().locator("[data-suijin-home-submit]").is_enabled())
        page.screenshot(path=os.path.join(SHOTS, "03-attached.png"))

        # E — what the landing hands the console
        hero().locator("[data-suijin-home-submit]").click()
        page.wait_for_url(f"**/chats/{STUB}", timeout=15000)
        page.locator("textarea:visible").first.wait_for(timeout=20000)
        page.wait_for_timeout(800)
        create = captured.get("create") or {}
        check("create carries the chosen Recon + Pentest, not the defaults", create.get("permissions") == "recon" and create.get("mode") == "pentest", create)
        check("create holds the agent until the files are uploaded", create.get("spawn") is False, create.get("spawn"))
        check("the prompt is the title", create.get("title") == "Ping from the landing journey\nsecond line continues", repr(create.get("title")))
        check("the scope file is uploaded to the new session's inbox", (captured.get("files") or {}).get("files") == [{"name": "scope.md", "content": SCOPE_TEXT}], captured.get("files"))
        message = captured.get("message") or {}
        check("the prompt is posted as the first message", message.get("text") == create.get("title") and message.get("attachments") == [], message)
        unexpected = [call for call in captured.get("unexpected", []) if "runtime" not in call]
        check("no other state-changing call is made", not unexpected, unexpected)
        check("the landing is gone and the chat owns the pane", page.locator("iframe[data-suijin-hero-frame]").count() == 0)
        check("the chat shows the submitted prompt", page.get_by_text("Ping from the landing journey", exact=False).count() >= 1)
        box = page.locator("textarea:visible").first
        check("the in-chat composer is present and editable", box.count() >= 1 and box.is_editable())
        page.screenshot(path=os.path.join(SHOTS, "04-after-submit.png"))

        # F — back on the landing
        page.get_by_text("New engagement", exact=True).first.click()
        composer().wait_for(timeout=20000)
        page.wait_for_timeout(500)
        check("New engagement returns to the landing", page.locator("iframe[data-suijin-hero-frame]").count() == 1)
        check("the persisted selection syncs into the landing (Pentest / Recon)",
              hero().locator('[aria-label="Mode"]').inner_text().strip().startswith("Pentest") and hero().locator("[data-suijin-home-permissions]").inner_text().strip() == "Recon",
              (hero().locator('[aria-label="Mode"]').inner_text(), hero().locator("[data-suijin-home-permissions]").inner_text()))
        check("an accepted submit clears the draft", composer().input_value() == "")

        # G — History opens the console's chat rail
        hero().locator("[data-suijin-home-history]").click()
        page.wait_for_timeout(500)
        check("History opens the chat rail", page.get_by_role("dialog").count() >= 1)
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)

        # H — 0's prebuilt model catalog, fed by Suijin's model list
        hero().locator("[data-suijin-home-model]").click()
        page.wait_for_timeout(1000)
        check("the model menu opens", popup().is_visible())
        rows_before = popup().locator("[data-model-option]").count()
        popup().locator("input").first.fill("glm-4.7")
        page.wait_for_timeout(500)
        rows = popup().locator("[data-model-option]").all_inner_texts()
        check("the catalog lists Suijin's models and filters", rows_before > 5 and any("glm-4.7" in row for row in rows), (rows_before, rows[:3]))
        page.screenshot(path=os.path.join(SHOTS, "05-model-menu.png"))
        popup().locator("[data-model-option]", has_text="glm-4.7").first.click()
        page.wait_for_timeout(700)
        patch = captured.get("runtimePatch") or {}
        check("picking a model saves provider + model through the runtime endpoint", patch == {"providerId": "zai", "model": "glm-4.7"}, patch)
        check("the control shows the new model", hero().locator("[data-suijin-home-model]").inner_text().startswith("glm-4.7"), hero().locator("[data-suijin-home-model]").inner_text())

        # I — a suggestion fills the composer
        hero().locator('[data-suijin-home-suggestion="Recon a target"]').click()
        page.wait_for_timeout(300)
        check("a suggestion fills the composer", composer().input_value() == "Run passive recon on ", composer().input_value())

        # J — a link inside the landing routes the console
        hero().locator("[data-suijin-home-model]").click()
        page.wait_for_timeout(600)
        popup().get_by_text("Model settings").click()
        page.wait_for_timeout(900)
        check("Model settings takes the console to /models", re.sub(r"\?.*$", "", page.url).endswith("/models"), page.url)
        page.go_back()
        composer().wait_for(timeout=20000)
        page.wait_for_timeout(500)

        # K — keyboard
        composer().fill("")
        hero().locator('[aria-label="Mode"]').click()
        page.wait_for_timeout(350)
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
        check("Enter inside an open menu never submits", "message" not in captured or captured["message"].get("text") != "")
        before = hero().locator('[aria-label="Mode"]').inner_text().strip()
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_timeout(450)
        after = hero().locator('[aria-label="Mode"]').inner_text().strip()
        check("ArrowDown + Enter selects and closes the menu", popup().count() == 0 and after != before, (before, after))
        check("focus then moves to the composer", landing_frame().evaluate("document.activeElement?.hasAttribute('data-suijin-home-textarea')") is True)

        # L — a reload never eats a draft
        composer().fill("")
        composer().type("keep this draft")
        page.wait_for_timeout(200)
        page.reload(wait_until="networkidle")
        composer().wait_for(timeout=20000)
        page.wait_for_timeout(500)
        check("a draft survives a reload", composer().input_value() == "keep this draft", composer().input_value())
        check("a selector choice survives a reload (not just the last submitted one)",
              hero().locator('[aria-label="Mode"]').inner_text().strip() == after,
              (after, hero().locator('[aria-label="Mode"]').inner_text()))
        composer().fill("")

        # N — nothing invisible sits on top of a control
        composer().type("hit test")
        page.wait_for_timeout(250)
        hit_test = landing_frame().evaluate(
            """() => ['[data-suijin-home-attach]','[data-suijin-home-permissions]','[data-suijin-home-model]','[data-suijin-home-history]',
                      '[data-suijin-home-submit]','[aria-label="Mode"]','[data-suijin-home-suggestion]']
              .flatMap(selector => Array.from(document.querySelectorAll(selector)).map(element => {
                const r = element.getBoundingClientRect();
                const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
                return {selector, ok: element.disabled || top === element || element.contains(top)};
              }))"""
        )
        blocked = [item["selector"] for item in hit_test if not item["ok"]]
        check("every control is on top at its centre", not blocked, blocked)

        # R — the console cannot create the session: the landing keeps the prompt
        captured["fail_create"] = True
        composer().fill("This must survive a failed start")
        page.wait_for_timeout(200)
        hero().locator("[data-suijin-home-submit]").click()
        page.wait_for_timeout(1800)
        check("a failed start keeps the prompt", composer().input_value() == "This must survive a failed start", composer().input_value())
        check("a failed start says why", "Couldn't start the engagement" in hero().locator("[data-suijin-home-notice]").inner_text())
        check("send is usable again after a failure", hero().locator("[data-suijin-home-submit]").is_enabled())
        page.screenshot(path=os.path.join(SHOTS, "06-failed-start.png"))
        captured["fail_create"] = False

        errors = [entry for entry in logs
                  if (entry[0] in ("pageerror", "error") and "500 (Internal Server Error)" not in entry[1]  # the failure R injects
                      or entry[0] == "requestfailed" and "ERR_ABORTED" not in entry[1])]
        check("no page errors or failed requests during the whole journey", not errors, errors[:4])
        browser.close()

    passed = sum(1 for ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed · screenshots in {SHOTS}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
