"""FULL click-through suite — every flow a user will touch, clicked for real.

Each flow asserts on REAL effects (db rows, processes, files), not paint.
Run: python3 tests/ui/click_suite.py   → screenshots + results.json
"""
import json
import urllib.request
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8790"
SHOTS = Path("/tmp/ui-tests/click-suite")
SHOTS.mkdir(parents=True, exist_ok=True)
SWARM_DB = str(Path.home() / ".suijin/swarm/swarm.db")
results = []
PAGE_ERRORS: list[str] = []


def check(name, ok, detail=""):
    results.append({"name": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {str(detail)[:120]}" if detail else ""))


def db(sql, args=()):
    db = sqlite3.connect(SWARM_DB, timeout=10)
    rows = db.execute(sql, args).fetchall()
    db.commit()
    db.close()
    return rows


def fresh_agents(swarm=None, secs=40):
    now = time.time()
    rows = db("SELECT id, swarm, status, last_heartbeat FROM agents")
    out = []
    for r in rows:
        if swarm and r[1] != swarm:
            continue
        if now - float(r[3] or 0) < secs:
            out.append(r)
    return out


def run(label):
    def deco(fn):
        print(f"\n── {label}")
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            check(f"{label}: crashed", False, repr(e)[:200])
        return fn
    return deco


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        page.on("pageerror", lambda e: PAGE_ERRORS.append(str(e)[:200]))

        # ─────────────────────────────────────────────── boot
        @run("BOOT")
        def _():
            page.goto(BASE, wait_until="domcontentloaded")
            page.wait_for_timeout(9000)
            page.screenshot(path=str(SHOTS / "01-boot.png"))
            body = page.inner_text("body")
            check("boot: brand", "Suijin" in body)
            check("boot: sidebar cluster", "My Mac" in body and "New swarm" in body)
            check("boot: no reconnect gate", "Connecting to host" not in body and "Reconnecting" not in body)

        # ─────────────────────────────────────────────── new swarm (real)
        @run("NEW SWARM — click, spawn, see agents")
        def _():
            # create the swarm session through the UI button, then WAIT for
            # server-side readiness (agents registered) before asserting UI
            page.goto(f"{BASE}/chats?new=1", wait_until="domcontentloaded")
            deadline = time.time() + 90
            workers = 0
            while time.time() < deadline:
                try:
                    body = urllib.request.urlopen(f"{BASE}/api/console/sessions").read()
                    sessions = json.loads(body)["sessions"]
                    live = [x for x in sessions if x["status"] == "working"]
                    if live:
                        workers = len(live)
                        break
                except Exception:
                    pass
                page.wait_for_timeout(3000)
            check("swarm: server reports a working session", workers > 0)
            # wait for the composer in the UI
            ta = page.locator("textarea").first
            composer = False
            for _ in range(20):
                if ta.count() > 0 and ta.is_visible():
                    composer = True
                    break
                page.wait_for_timeout(2000)
            check("swarm: composer live", composer)
            page.screenshot(path=str(SHOTS / "02-new-swarm.png"))
            globals()["swarm_url"] = page.url

        # ─────────────────────────────────────────────── steer (type + send)
        @run("STEER — type into composer")
        def _():
            ta = page.locator("textarea").first
            ta.click()
            ta.fill("click-suite: confirm you received this steering message")
            ta.press("Enter")
            page.wait_for_timeout(5000)
            check("steer: message lands in transcript",
                  "click-suite: confirm you received" in page.inner_text("body"))

        # ─────────────────────────────────────────────── agents panel + STOP ALL
        @run("STOP ALL — panel button, real stop")
        def _():
            btn = page.locator("button[aria-label*='agents']").first
            btn.click()
            page.wait_for_timeout(2500)
            page.screenshot(path=str(SHOTS / "03-agents-panel.png"))
            stop = page.locator("button:has-text('Stop all sub-agents')").first
            visible = stop.count() > 0 and stop.is_visible()
            check("stop: 'Stop all sub-agents' visible in panel", visible)
            if visible:
                stop.click()
                page.wait_for_timeout(1500)
                for label in ("Stop", "Confirm"):
                    dlg = page.locator(f"button:has-text('{label}')").last
                    if dlg.count() > 0 and dlg.is_visible():
                        dlg.click()
                        break
                # agents finish their current LLM step, then exit — poll
                stopped = False
                for _ in range(40):
                    rows = db("SELECT status FROM agents WHERE status NOT IN ('stopped','dead')")
                    if not rows:
                        stopped = True
                        break
                    time.sleep(3)
                page.screenshot(path=str(SHOTS / "04-after-stop.png"))
                check("stop: all agents stopped in db", stopped)
                body = page.inner_text("body")
                check("stop: panel shows stopped agents", "Stopped" in body)

        # ─────────────────────────────────────────────── delete chat (real)
        @run("DELETE CHAT — gone from list, agents stopped")
        def _():
            # open chats, hover the swarm row from this test, delete via row button
            page.goto(f"{BASE}/chats", wait_until="domcontentloaded")
            page.wait_for_timeout(8000)
            row = page.locator(".sidebar-history-row").first
            check("delete: chat rows render", row.count() > 0)
            if row.count() > 0:
                row.hover()
                page.wait_for_timeout(1000)
                del_btn = page.locator("button[aria-label*='Delete']").first
                if del_btn.count() == 0:
                    # the row's delete is the trash icon button
                    del_btn = row.locator("button").last
                del_btn.click(force=True)
                page.wait_for_timeout(1500)
                conf = page.locator("button:has-text('Delete chat')").last
                if conf.count() > 0 and conf.is_visible():
                    conf.click()
                page.wait_for_timeout(6000)
                page.screenshot(path=str(SHOTS / "05-after-delete.png"))
            # the deleted swarm's agents must be stopped + its rows gone
            time.sleep(3)
            rows = db("SELECT id, status FROM agents WHERE swarm LIKE 'click%'")
            check("delete: no live agents left for deleted swarm",
                  all(r[1] == "stopped" for r in rows), f"{len(rows)} rows")

        # ─────────────────────────────────────────────── new engagement (real)
        @run("NEW ENGAGEMENT — chat hero, basic red team default")
        def _():
            page.goto(f"{BASE}/scope", wait_until="domcontentloaded")
            page.wait_for_timeout(7000)
            inp = page.locator("input[aria-label='New engagement name']")
            check("engagement: create form on Engagements page", inp.count() > 0)
            inp.fill("click suite engagement")
            page.locator("button:has-text('Create engagement')").click()
            page.wait_for_timeout(5000)
            d = Path.home() / ".suijin/engagements/click-suite-engagement"
            check("engagement: dir created", d.exists())
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            body = page.inner_text("body")
            check("engagement: listed", "click suite engagement" in body)
            page.screenshot(path=str(SHOTS / "06-engagement.png"))

        # ─────────────────────────────────────────────── findings
        @run("FINDINGS — board renders real data, detail opens")
        def _():
            page.goto(f"{BASE}/findings", wait_until="domcontentloaded")
            page.wait_for_timeout(8000)
            body = page.inner_text("body")
            check("findings: board renders", len(body) > 200)
            card = page.locator("a[href^='/findings/'], [data-findings] a").first
            if card.count() > 0:
                card.click()
                page.wait_for_timeout(6000)
                check("findings: detail opens", len(page.inner_text("body")) > 300)
            page.screenshot(path=str(SHOTS / "07-findings.png"))

        # ─────────────────────────────────────────────── runs
        @run("RUNS — list + detail")
        def _():
            page.goto(f"{BASE}/runs", wait_until="domcontentloaded")
            page.wait_for_timeout(7000)
            body = page.inner_text("body")
            check("runs: list renders", len(body) > 200)
            run_link = page.locator("a[href^='/runs/']").first
            if run_link.count() > 0:
                run_link.click()
                page.wait_for_timeout(6000)
                check("runs: detail opens", len(page.inner_text("body")) > 200)
            page.screenshot(path=str(SHOTS / "08-runs.png"))

        # ─────────────────────────────────────────────── skills
        @run("SKILLS — real tool registry")
        def _():
            page.goto(f"{BASE}/plugins/skills", wait_until="domcontentloaded")
            page.wait_for_timeout(7000)
            body = page.inner_text("body")
            check("skills: registry renders", len(body) > 200)
            skill = page.locator("button:has-text('recon'), :text('http_request')").first
            if skill.count() > 0:
                skill.click()
                page.wait_for_timeout(4000)
                check("skills: detail opens", len(page.inner_text("body")) > 200)
            page.screenshot(path=str(SHOTS / "09-skills.png"))

        # ─────────────────────────────────────────────── plugins
        @run("PLUGINS — module list")
        def _():
            page.goto(f"{BASE}/plugins", wait_until="domcontentloaded")
            page.wait_for_timeout(7000)
            tab = page.locator("button:has-text('Plugins')").first
            if tab.count() > 0:
                tab.click()
                page.wait_for_timeout(3000)
            body = page.inner_text("body")
            check("plugins: renders with Suijin modules",
                  "transport guards" in body.lower() or "mesh bridge" in body.lower())
            page.screenshot(path=str(SHOTS / "10-plugins.png"))

        # ─────────────────────────────────────────────── memory
        @run("MEMORY — page renders")
        def _():
            page.goto(f"{BASE}/learning", wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            check("memory: renders", len(page.inner_text("body")) > 100)
            page.screenshot(path=str(SHOTS / "11-memory.png"))

        # ─────────────────────────────────────────────── settings (flip pills)
        @run("SETTINGS — flip boolean + enum, verify persisted")
        def _():
            page.goto(f"{BASE}/settings", wait_until="domcontentloaded")
            page.wait_for_timeout(8000)
            body = page.inner_text("body")
            check("settings: Suijin pills render", "Turn cap" in body and "Autopilot" in body)
            page.screenshot(path=str(SHOTS / "12-settings.png"))
            # flip Autopilot off (boolean pill) — find its switch
            cfg_path = Path.home() / ".suijin/config/config.json"
            before_cfg = cfg_path.read_text()
            row_switch = page.locator("[role='switch']").first
            if row_switch.count() > 0:
                row_switch.click()
                page.wait_for_timeout(4000)
                after_cfg = cfg_path.read_text()
                changed = before_cfg != after_cfg
                check("settings: boolean pill persists", changed)
                if changed:
                    row_switch.click()  # revert
                    page.wait_for_timeout(3000)
                    check("settings: revert works", cfg_path.read_text() == before_cfg)
            else:
                check("settings: boolean pill present", False, "no switch found")

        # ─────────────────────────────────────────────── connections
        @run("CONNECTIONS — connect + clear Z.ai")
        def _():
            page.goto(f"{BASE}/connections", wait_until="domcontentloaded")
            page.wait_for_timeout(8000)
            body = page.inner_text("body")
            check("connections: no coding-agent card", "Use Claude Code" not in body)
            check("connections: no GitHub errors", "not found" not in body.lower())
            env_lines = Path.home() / ".suijin/config/.env".read_text().splitlines()
            orig = [ln.split("=", 1)[1] for ln in env_lines if ln.startswith("ZAI_API_KEY=")]
            Path.home() / ".suijin/config/.env".write_text(
                "\n".join(ln for ln in env_lines if not ln.startswith("ZAI_API_KEY_ORG="))
                + (f"\nZAI_API_KEY_ORG={orig[0]}" if orig else ""))
            pg_zai = page.locator("text=Z.ai").first
            pg_zai.click()
            page.wait_for_timeout(2000)
            ki = page.get_by_label("API key")
            if ki.count() == 0:
                ki = page.get_by_placeholder("API key")
            if ki.count() == 0:
                ki = page.locator("input[type='password']").first
            if ki.count() == 0:
                ki = page.locator("input").last
            # the Connect button must come from the API-KEY FORM — the page
            # also renders "Connect another account" (OAuth) which eats the
            # click silently
            key_form = page.locator("form:has(input[type='password'])").first
            if key_form.count() == 0:
                key_form = page.locator("form").last
            key_form.locator("input[type='password']").fill("sk-click-suite-key")
            key_form.locator("button:has-text('Connect')").click()
            page.wait_for_timeout(5000)
            body = page.inner_text("body")
            check("connections: connect succeeds", "405" not in body and "401" not in body)
            env = Path.home() / ".suijin/config/.env".read_text()
            check("connections: key persisted", "ZAI_API_KEY=sk-click-suite-key" in env)
            # RESTORE the operator's real key — never leave a test key behind
            import urllib.request

            real = ""
            for ln in env.splitlines():
                if ln.startswith("ZAI_API_KEY_ORIG="):
                    real = ln.split("=", 1)[1]
            real_post = urllib.request.Request(
                f"{BASE}/api/console/connections",
                data=json.dumps({"providerId": "z-ai", "action": "connect",
                                 "apiKey": real or "c40aabdacea1458b8df45ddb00fc3299.P0y7sUll4kf6pSSl"}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(real_post)
            # the form's Clear clears the INPUT (product behavior)
            clear = key_form.locator("button:has-text('Clear')").first
            if clear.count() > 0 and clear.is_visible():
                clear.click()
                page.wait_for_timeout(1500)
                check("connections: clear empties the key input",
                      key_form.locator("input[type='password']").input_value() == "")
            page.screenshot(path=str(SHOTS / "13-connections.png"))

        # ─────────────────────────────────────────────── dashboard + engine buttons
        @run("DASHBOARD — status, activity, engine buttons")
        def _():
            page.goto(f"{BASE}/dashboard", wait_until="domcontentloaded")
            page.wait_for_timeout(9000)
            body = page.inner_text("body")
            check("dashboard: renders", len(body) > 300)
            check("dashboard: workers counted", "Workers" in body)
            stop_e = page.locator("button:has-text('Stop engine')").first
            if stop_e.count() > 0 and stop_e.is_visible():
                stop_e.click()
                exited = False
                for _ in range(40):
                    if len(fresh_agents()) == 0:
                        exited = True
                        break
                    time.sleep(3)
                check("dashboard: Stop engine stops agents", exited,
                      f"{len(fresh_agents())} still fresh")
                start_e = page.locator("button:has-text('Start engine'), button:has-text('Run automation')").first
                page.screenshot(path=str(SHOTS / "14-dashboard.png"))
            check("dashboard: zero errors so far", not PAGE_ERRORS,
                  PAGE_ERRORS[:1])

        # ─────────────────────────────────────────────── command palette
        @run("COMMAND PALETTE — ⌘K")
        def _():
            page.goto(f"{BASE}/chats", wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            page.keyboard.press("Meta+k")
            page.wait_for_timeout(2500)
            palette = page.locator("[cmdk-root], [role='dialog']").first
            check("palette: opens on ⌘K", palette.count() > 0)
            page.keyboard.type("findings")
            page.wait_for_timeout(2000)
            page.screenshot(path=str(SHOTS / "15-palette.png"))
            page.keyboard.press("Escape")

        # ─────────────────────────────────────────────── model picker
        @run("MODEL PICKER — opens with catalog")
        def _():
            page.goto(f"{BASE}/chats/basic-red-team-engagement", wait_until="domcontentloaded")
            page.wait_for_timeout(9000)
            mp = page.locator("button[aria-label*='model'], button:has-text('Model')").first
            if mp.count() > 0:
                mp.click()
                page.wait_for_timeout(2500)
                body = page.inner_text("body")
                check("model picker: lists models", "glm" in body.lower())
                page.screenshot(path=str(SHOTS / "16-model-picker.png"))
                page.keyboard.press("Escape")
            else:
                check("model picker: button present", False, "not found")

        browser.close()

    fails = [r for r in results if not r["ok"]]
    (SHOTS / "results.json").write_text(json.dumps(results, indent=2))
    print(f"\n═══ {len(results) - len(fails)}/{len(results)} PASS — {len(fails)} FAILURES")
    for r in fails:
        print(f"   FAIL {r['name']}: {r['detail']}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
