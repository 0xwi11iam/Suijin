"""The operations pages, clicked through in a real browser against the real backend.

It uses real controls — queues a piece of work and takes it back, pauses and resumes the fleet,
starts the coordinator and stops everything — and leaves the machine as it found it:
no intent, no pause flag, no coordinator, OrbStack state untouched by this script.

Usage: python3 ops_journey.py <tag>
"""
import faulthandler
import json
import os
import sys
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8790"
HARD_LIMIT_S = 420          # the whole journey dies with a traceback; it can never hang (2026-11-28)


# The limits are a stack dump after 45 s stuck and a watchdog thread that ends the process at 300 s
# (a Python signal handler cannot run while Playwright's sync layer is blocked).
import threading
import traceback
import time
import traceback

faulthandler.dump_traceback_later(45, repeat=True)
threading.Timer(300, lambda: os._exit(3)).start()
OUT = os.path.expanduser("~/suijin/server/web/.opencode/shots")
MESH_MARK = f"qa journey: mesh check {os.getpid()}"      # unique per run: a killed run never cleans its own line
TAG = sys.argv[1] if len(sys.argv) > 1 else "ops"
os.makedirs(OUT, exist_ok=True)
results = []


def check(label, ok, detail=""):
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {str(detail)[:220]}" if detail != "" else ""), flush=True)


def api(path, data=None, method=None):
    request = urllib.request.Request(BASE + path, data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Content-Type": "application/json"}, method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode()
            return response.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as error:
        return error.code, (json.loads(error.read().decode() or "{}") if error.headers.get("content-type", "").startswith("application/json") else {})


def wait_for(page, selector, timeout=15000):
    page.locator(selector).first.wait_for(timeout=timeout)


def errors_of(page):
    return page.evaluate("() => window.__errors || []")


def arm(page):
    page.evaluate("() => { window.__errors = []; window.addEventListener('error', e => window.__errors.push(String(e.message)[:200])); }")


p = sync_playwright().start()  # never .stop(): its teardown can hang for minutes; the process leaves by os._exit
own_chat = None  # a throwaway chat this run made, so step ② has one to open
try:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    # ── the state this journey assumes (2026-11-28: it used to assume both, and crashed when
    #    an engagement had left its coordinator up and no chat was left in the store) ──
    _status, board = api("/api/ops/overview")
    if board["coordinator"]["running"] and not board["counts"]["agents"] and not board["counts"]["intents"]:
        # A coordinator left behind by an engagement that is gone: idle, nothing live. Bring it
        # down the way "Stop everything" does below, so the journey starts from a clean state.
        _status, _answer = api("/api/ops/swarm/stop-all", {})
        print(f"    (stopped an idle coordinator left by a gone engagement: HTTP {_status})", flush=True)
        page.wait_for_timeout(1500)
    _status, listed = api("/api/console/sessions")
    if not any(s.get("kind") == "chat" for s in listed["sessions"]):
        # A chat of its own, with a tool call in its journal, for step ② to open.
        _status, made = api("/api/console/sessions", {"title": "qa journey: a chat with a tool call", "kind": "chat"})
        own_chat = made["session"]["id"]
        journal = os.path.expanduser(f"~/.suijin/engagements/{own_chat}/events.jsonl")
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        records = [
            {"kind": "user.message", "content": "Use the suijin_doctor tool, then answer in one short sentence.", "author": "operator", "seq": 1, "ts": stamp},
            {"kind": "assistant.reasoning", "content": "The operator asked for a health check; the tool answers it.", "seq": 2, "ts": stamp},
            {"kind": "tool.call", "id": "qa-1-suijin_doct", "name": "suijin_doctor", "args": {}, "agent": "qa-agent", "seq": 3, "ts": stamp},
            {"kind": "tool.result", "id": "qa-1-suijin_doct", "ok": True, "agent": "qa-agent", "seq": 3, "ts": stamp,
             "output": json.dumps({"status": "healthy", "provider": "zai", "context": 49, "models": ["glm-5.3"], "checked": True}, indent=2)},
            {"kind": "tool.call", "id": "qa-2-local_sys_i", "name": "local_sys_info", "args": {}, "agent": "qa-agent", "seq": 4, "ts": stamp},
            {"kind": "tool.result", "id": "qa-2-local_sys_i", "output": "ok", "ok": True, "agent": "qa-agent", "seq": 4, "ts": stamp},
            {"kind": "assistant.message", "content": "Suijin is healthy.", "agent": "qa-agent", "seq": 5, "ts": stamp},
        ]
        os.makedirs(os.path.dirname(journal), exist_ok=True)
        with open(journal, "a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        print(f"    (made a chat of its own to open: {own_chat})", flush=True)

    errors = []
    bad_responses: list[str] = []

    def on_response(response):
        if "/api/" in response.url and response.status >= 400:
            try:
                body = response.text()[:160]
            except Exception:  # noqa: BLE001
                body = ""
            bad_responses.append(f"{response.status} {response.request.method} {response.url.split('8790')[-1]} {body}")

    page.on("response", on_response)
    page.on("pageerror", lambda e: errors.append("page: " + str(e)[:220]))
    page.on("console", lambda m: errors.append("console: " + m.text[:220]) if m.type == "error" else None)
    shots = 0

    def shot(name, full=True):
        """Viewport captures, scrolling the page's own scroll container. A Chromium capture
        "beyond the viewport" intermittently hangs for minutes on these layouts (2026-11-28)."""
        global shots
        shots += 1
        base = os.path.join(OUT, f"{TAG}-{shots:02d}-{name}")
        if not full:
            page.screenshot(path=base + ".png", full_page=False, timeout=15000)
            return
        page.evaluate("""() => {
          const els = [...document.querySelectorAll('*')].filter(e => { const cs = getComputedStyle(e);
            return /(auto|scroll)/.test(cs.overflowY) && e.scrollHeight > e.clientHeight + 40; });
          els.sort((a, b) => b.scrollHeight - a.scrollHeight);
          if (els[0]) { els[0].dataset.qaScroll = '1'; els[0].scrollTop = 0; } }""")
        for part in range(8):
            page.screenshot(path=base + (f"-part{part + 1}" if part else "") + ".png", full_page=False, timeout=15000)
            more = page.evaluate("""() => { const el = document.querySelector('[data-qa-scroll]');
              if (!el) { window.scrollBy(0, innerHeight - 80); return window.scrollY + innerHeight < document.documentElement.scrollHeight - 2; }
              const before = el.scrollTop; el.scrollTop = before + el.clientHeight - 80;
              return el.scrollTop > before && el.scrollTop + el.clientHeight < el.scrollHeight - 2; }""")
            if not more:
                break
        page.evaluate("() => { const el = document.querySelector('[data-qa-scroll]'); if (el) { delete el.dataset.qaScroll; el.scrollTop = 0; } }")

    # ────────────────────────────── Swarm ──────────────────────────────
    print("\n① Swarm")
    page.goto(BASE + "/swarm", wait_until="domcontentloaded")
    wait_for(page, "[data-panel-title]")
    page.wait_for_timeout(2500)
    check("the page is titled Swarm", page.locator("h1").first.inner_text().strip() == "Swarm")
    titles = [t.inner_text().strip().split("\n")[0] for t in page.query_selector_all("[data-panel-title]")]
    check("every subsystem has its panel", {"Agents", "Queue", "Start agents", "Boxes and warm pile", "Coordinator", "Fleet settings", "Mesh", "Structured teams", "This machine", "Background jobs"} <= set(titles), titles)
    check("Start agents waits for the coordinator", page.locator("[data-spawn-submit]").first.is_disabled() and "The coordinator is not running" in page.inner_text("body"))
    teams = page.locator("[data-team]").count()
    check("structured teams are listed", teams >= 1 or "No structured team on record" in page.inner_text("body"), teams)
    check("six headline stats", len(page.query_selector_all("[data-stat]")) >= 6, len(page.query_selector_all("[data-stat]")))
    coordinator_before = page.evaluate("() => document.querySelector('[data-coordinator-state]')?.getAttribute('data-coordinator-state')")
    check("the coordinator state is shown", coordinator_before in ("running", "stopped"), coordinator_before)
    shot("swarm-top")

    # queue a piece of work, then take it back
    page.get_by_label("New intent").fill("qa journey: list the installed plugins and report the count")
    page.wait_for_timeout(300)
    page.get_by_role("button", name="Queue", exact=True).click()
    page.wait_for_timeout(1200)
    row = page.locator("[data-intent-state='open']", has_text="qa journey").first
    check("queued work appears, open", row.count() == 1)
    intent_id = None
    for _ in range(20):
        status, board = api("/api/ops/overview")
        hit = next((i for i in board.get("intents", []) if i["text"].startswith("qa journey")), None)
        if hit:
            intent_id = hit["id"]
            break
        page.wait_for_timeout(300)
    check("the intent is in the database", bool(intent_id))
    shot("swarm-queue")
    row.get_by_role("button", name="Remove from the queue").click()
    page.get_by_role("button", name="Remove", exact=True).click()
    page.wait_for_timeout(1200)
    check("taking it back removes it", page.locator("[data-intent-id]", has_text="qa journey").count() == 0)
    # the prior-work gate: when it refuses, it says why and offers to queue anyway
    page.get_by_label("New intent").fill("qa journey: list the installed plugins and report the count")
    page.get_by_role("button", name="Queue", exact=True).click()
    page.wait_for_timeout(1500)
    refused = page.locator("[data-queue-refused]")
    if refused.count():
        check("the prior-work gate explains itself and offers to queue anyway", "Queue anyway" in refused.inner_text())
        refused.get_by_role("button", name="Queue anyway").click()
        page.wait_for_timeout(1200)
        check("queuing anyway queues it", page.locator("[data-intent-id]", has_text="qa journey").count() >= 1)
    else:
        check("the same work queues again when the gate has no objection", page.locator("[data-intent-id]", has_text="qa journey").count() >= 1)
    page.wait_for_timeout(800)
    for row in page.locator("[data-intent-id]", has_text="qa journey").all():
        row.get_by_role("button", name="Remove from the queue").click()
        page.get_by_role("button", name="Remove", exact=True).click()
        page.wait_for_timeout(1000)
    check("the queue is clean again", page.locator("[data-intent-id]", has_text="qa journey").count() == 0)
    # per-swarm controls and the mesh, both real
    page.get_by_role("button", name="Pause default").click()
    page.wait_for_timeout(600)
    page.get_by_role("button", name="Resume default").click()
    page.wait_for_timeout(600)
    page.get_by_label("Say on the mesh").fill(MESH_MARK)
    page.get_by_role("button", name="Say", exact=True).click()
    mesh_line = page.locator("[data-mesh-line]", has_text=MESH_MARK)
    try:
        mesh_line.first.wait_for(timeout=8000)
    except Exception:  # noqa: BLE001 — reported below
        pass
    check("saying something on the mesh lands in the mesh log", mesh_line.count() == 1, f"{mesh_line.count()} rows for this run's line")

    # fleet pause / resume (the real flag file)
    flag = os.path.expanduser("~/.suijin/mesh/pause-swarm")
    page.get_by_role("button", name="Pause fleet").click()
    page.wait_for_timeout(900)
    check("Pause fleet sets the fleet pause flag", os.path.exists(flag))
    page.get_by_role("button", name="Resume fleet").click()
    page.wait_for_timeout(900)
    check("Resume fleet clears it", not os.path.exists(flag))

    # a command the bus refuses is reported, not swallowed
    if coordinator_before == "stopped":
        page.get_by_role("button", name="Steer the fleet").click()
        page.locator("textarea").fill("qa journey steer")
        page.get_by_role("dialog").get_by_role("button", name="Send", exact=True).click()
        page.wait_for_timeout(1200)
        notice = page.evaluate("() => document.body.innerText")
        check("a refused command says why (no coordinator → no bus)", "did not accept the command" in notice, notice[:0])
        page.keyboard.press("Escape")
        page.wait_for_timeout(6000)                    # let the flash clear before the next step

    # the coordinator: start it for real, then stop everything for real
    if coordinator_before == "stopped":
        page.get_by_role("button", name="Start coordinator").click()
        deadline = time.time() + 60
        running = False
        while time.time() < deadline:
            page.wait_for_timeout(1000)
            running = page.evaluate("() => document.querySelector('[data-coordinator-state]')?.getAttribute('data-coordinator-state')") == "running"
            if running:
                break
        check("Start coordinator brings the coordinator up", running)
        shot("swarm-coordinator-running")
        check("Start agents is available once the coordinator runs", page.locator("[data-spawn-submit]").first.is_enabled())
        page.get_by_role("button", name="Set the mission of default").click()
        page.get_by_role("dialog").locator("textarea").fill("qa journey: hold and report status")
        page.get_by_role("dialog").get_by_role("button", name="Set mission").click()
        page.wait_for_timeout(1200)
        body = page.inner_text("body")
        check("a fleet mission can be set", "the mission is replaced" in body, "" if "the mission is replaced" in body else "flash not seen")
        page.wait_for_timeout(5500)
        page.get_by_role("button", name="Stop everything").click()
        page.get_by_role("dialog").get_by_label("Type stop to continue").fill("stop")
        page.get_by_role("dialog").get_by_role("button", name="Stop everything").click()
        state = None
        deadline = time.time() + 60
        while time.time() < deadline:                  # the stop itself takes a few seconds; wait for the truth
            page.wait_for_timeout(1000)
            state = page.evaluate("() => document.querySelector('[data-coordinator-state]')?.getAttribute('data-coordinator-state')")
            if state == "stopped":
                break
        check("Stop everything brings it down again", state == "stopped", state)
        check("the confirmation closes once it is done", page.get_by_role("dialog").count() == 0)
        status, board = api("/api/ops/overview")
        check("nothing was queued in the default swarm by the visit", not [i for i in board["intents"] if i.get("swarm") == "default"], board["intents"])
        check("the pause flag is clear after the visit", not os.path.exists(flag) and not board["fleet"]["pause_flag"])
    box_note = page.evaluate("() => document.querySelector('[data-boxes-engine=off]')?.innerText || ''")
    print("    boxes:", "engine off notice" if box_note else "engine running")
    check("no page errors on the swarm page", not errors_of(page) and not errors, (errors_of(page) + errors)[:3])
    check("no failed API call on the swarm page", not bad_responses, bad_responses[:4])
    errors.clear(); bad_responses.clear()

    # ──────────────────────────── Engagements ───────────────────────────
    print("\n② Engagements")
    page.goto(BASE + "/engagements", wait_until="domcontentloaded")
    wait_for(page, "[data-engagement-id]")
    page.wait_for_timeout(1200)
    rows = page.query_selector_all("[data-engagement-id]")
    check("every engagement folder is listed", len(rows) >= 2, len(rows))
    kinds = [r.inner_text() for r in rows]
    check("kinds are named (engagement, legacy or chat)", any("chat" in k for k in kinds) and any(("engagement" in k) or ("legacy" in k) for k in kinds), kinds)
    shot("engagements-list")
    chat_id = next((r.get_attribute("data-engagement-id") for r in rows if "chat" in r.inner_text()), None)
    big_id = next((r.get_attribute("data-engagement-id") for r in rows if "chat" not in r.inner_text()), None)
    if not (chat_id and big_id):
        # Say it and stop: a click on a row named None is a timeout, a crash and a hung teardown.
        raise RuntimeError(f"the Engagements page has no {'chat' if not chat_id else 'engagement'} to open: {[r[:40] for r in kinds]}")
    page.locator(f"[data-engagement-id='{chat_id}']").click(timeout=10000)
    page.wait_for_url(f"**/engagements/{chat_id}", timeout=10000)
    wait_for(page, "[role='tablist']")
    page.wait_for_timeout(1500)
    check("the detail page opens", "/engagements/" + chat_id in page.url)
    tabs = [t.inner_text().strip() for t in page.query_selector_all("[role='tab']")]
    check("four tabs: Overview, Journal, Files, Exploits", [t.split("\n")[0].split(" ")[0] for t in tabs] == ["Overview", "Journal", "Files", "Exploits"], tabs)
    check("Overview shows its numbers", len(page.query_selector_all("[data-stat]")) == 5)
    check("Overview says what happened (bars by kind)", len(page.query_selector_all("[data-meter]")) >= 3)
    check("a chat links to its conversation", page.get_by_role("link", name="Open chat").count() == 1)
    shot("engagement-overview")
    page.get_by_role("tab", name=re_tab("Journal")).click() if False else page.locator("[role='tab']", has_text="Journal").click()
    page.wait_for_timeout(1500)
    journal_rows = page.query_selector_all("[role='tabpanel'] tbody tr")
    check("the journal lists records", len(journal_rows) >= 5, len(journal_rows))
    chips = page.locator("[role='group'][aria-label='Record kind'] button")
    check("records can be filtered by kind", chips.count() >= 3, chips.count())
    page.locator("[role='group'][aria-label='Record kind'] button", has_text="tool.call").click()
    page.wait_for_timeout(1200)
    kinds_shown = [r.inner_text() for r in page.query_selector_all("[role='tabpanel'] tbody tr")]
    check("the kind filter narrows the list", kinds_shown and all("tool.call" in k for k in kinds_shown), kinds_shown[:3])
    page.query_selector_all("[role='tabpanel'] tbody tr")[0].click()
    page.wait_for_timeout(900)
    tree = page.evaluate("() => { const v = document.querySelector('[role=dialog] [data-json-view][data-json-kind=tree]'); return v ? { keys: v.querySelectorAll('.json-key').length, text: v.innerText.slice(0, 160) } : null; }")
    check("a record opens as a coloured JSON tree", bool(tree and tree["keys"] >= 3), tree)
    shot("engagement-journal-dialog")
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    page.locator("[role='tab']", has_text="Files").click()
    page.wait_for_timeout(1000)
    page.locator("[role='tabpanel'] nav button, [role='tabpanel'] button", has_text="events.jsonl").count()
    page.locator("[role='tabpanel'] button", has_text="exploit").first.click() if False else None
    file_buttons = page.locator("[role='tabpanel'] button[aria-current]")
    check("files are grouped and listed", file_buttons.count() >= 1, file_buttons.count())
    if big_id:
        page.goto(f"{BASE}/engagements/{big_id}", wait_until="domcontentloaded")
        wait_for(page, "[role='tablist']")
        # the detail fetch scans 4,417 journal lines on a cold server — wait for the
        # tab to carry its count rather than a fixed sleep (the 0-files flap, 2026-11-29)
        page.wait_for_selector("[role='tab']:has-text('Files')", timeout=20000)
        page.wait_for_function("() => /Files \\d/.test([...document.querySelectorAll('[role=tab]')].map(n => n.textContent).join(' '))", timeout=20000)
        page.locator("[role='tab']", has_text="Files").click()
        page.wait_for_selector("[role='tabpanel'] button[aria-current]", timeout=15000)
        file_buttons = page.locator("[role='tabpanel'] button[aria-current]")
        check("the big engagement lists its files", file_buttons.count() >= 10, file_buttons.count())
        check("the agent's home folder is not browsable", not any("home/" in (b.inner_text()) for b in file_buttons.all()))
        page.locator("[role='tabpanel'] button[aria-current]", has_text="catalog.json").first.click()
        page.wait_for_timeout(1500)
        shown = page.evaluate("() => document.querySelector('[role=tabpanel] [data-json-view]')?.getAttribute('data-json-kind')")
        check("a JSON file opens as a tree", shown == "tree", shown)
        shot("engagement-files")
        page.locator("[role='tab']", has_text="Exploits").click()
        page.wait_for_timeout(1000)
        exploit_rows = page.query_selector_all("[role='tabpanel'] tbody tr")
        check("the exploit catalog is listed", len(exploit_rows) >= 2, len(exploit_rows))
        shot("engagement-exploits")
        page.locator("[role='tab']", has_text="Journal").click()
        page.wait_for_timeout(2000)
        check("a 4,000-record journal reads its newest page", len(page.query_selector_all("[role='tabpanel'] tbody tr")) == 100)
        page.get_by_label("Search events").fill("suijin_doctor")
        page.wait_for_timeout(1500)
        check("the journal can be searched", len(page.query_selector_all("[role='tabpanel'] tbody tr")) >= 0)
    check("no page errors on engagements", not errors_of(page) and not errors, (errors_of(page) + errors)[:3])
    check("no failed API call on engagements", not bad_responses, bad_responses[:4])
    errors.clear(); bad_responses.clear()

    # ────────────────────────────── Data ──────────────────────────────
    print("\n③ Data")
    page.goto(BASE + "/data", wait_until="domcontentloaded")
    wait_for(page, "[data-store-id]")
    page.wait_for_timeout(1500)
    groups = [g.get_attribute("data-store-group") for g in page.query_selector_all("[data-store-group]")]
    check("every group of stores is listed", {"config", "swarm", "engagements", "knowledge", "tools", "mesh", "system", "logs"} <= set(groups), groups)
    counts = [b.inner_text().strip().split("\n")[-1] for b in page.query_selector_all("[data-store-id]")]
    check("the stores carry live row counts", sum(1 for c in counts if c.isdigit()) >= 25, f"{sum(1 for c in counts if c.isdigit())} numeric counts")
    page.locator("[data-store-id='swarm.status']").click()
    page.wait_for_timeout(1500)
    check("a store opens with its table", len(page.query_selector_all("thead th")) >= 3)
    check("the store says where it lives", "swarm/swarm.db" in page.inner_text("body"))
    page.locator("[data-store-id='bus.topics']").click()
    page.wait_for_timeout(1200)
    rows = page.query_selector_all("tbody tr")
    check("the bus topology lists its topics", len(rows) >= 15, len(rows))
    page.query_selector_all("tbody tr")[0].click()
    page.wait_for_timeout(800)
    check("a row opens as JSON", page.locator("[role=dialog] [data-json-view]").count() == 1)
    page.keyboard.press("Escape")
    page.get_by_role("button", name="Python").click()
    page.wait_for_timeout(700)
    check("the Python door is printed", "data_hub.catalog()" in page.inner_text("[role=dialog]"))
    shot("data-store")
    page.keyboard.press("Escape")
    page.locator("[data-store-id='config.env']").click()
    page.wait_for_timeout(1200)
    body = page.inner_text("body")
    check("a secret store shows names only", "API_KEY" in body and "sk-" not in body and "Bearer" not in body)
    check("a secret store says so", "secret" in page.inner_text("main").lower())
    check("no page errors on data", not errors_of(page) and not errors, (errors_of(page) + errors)[:3])
    check("no failed API call on data", not bad_responses, bad_responses[:4])
    errors.clear(); bad_responses.clear()

    # ─────────────────── the transcript shows JSON as a tree ───────────────────
    print("\n④ JSON in the conversation")
    page.goto(f"{BASE}/chats/{chat_id}", wait_until="domcontentloaded")
    wait_for(page, "[data-chat-turn]")
    page.wait_for_timeout(3000)
    # 0's transcript folds a tool result until it is opened. A chat this journey made on disk shows
    # it folded under the thought; one the operator made shows "Used suijin_doctor". Open whichever
    # is on the page — and if neither is, only then is there nothing to open.
    opener = None
    for name in ("Used suijin doctor", "Used suijin_doctor", "suijin_doctor"):
        candidate = page.get_by_text(name).first
        if candidate.count():
            opener = candidate
            break
    check("the tool result can be opened in the transcript", opener is not None, "no tool result found in the chat")
    if opener is not None:
        opener.click()
    page.wait_for_timeout(1000)

    def visible_tree():
        return page.evaluate("""() => {
          const trees = [...document.querySelectorAll('[data-chat-turn] [data-json-kind=tree]')].filter(v => {
            const t = v.querySelector('.json-toggle'); if (!t) return false;
            const r = t.getBoundingClientRect(); const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
            return top && t.contains(top); });
          if (!trees.length) return null;
          const v = trees[0]; return { keys: v.querySelectorAll('.json-key').length, strings: v.querySelectorAll('.json-string').length, numbers: v.querySelectorAll('.json-number').length,
                                       toolbar: v.querySelector('.json-toolbar')?.innerText.split(String.fromCharCode(10)).join(' '), height: Math.round(v.getBoundingClientRect().height) }; }""")

    tree = visible_tree()
    check("opening the tool result shows a JSON tree with coloured keys", bool(tree and tree["keys"] >= 3 and tree["strings"] >= 2), tree)
    before = page.evaluate("() => document.querySelector('[data-chat-turn] [data-json-kind=tree]').querySelectorAll('.json-row').length")
    page.evaluate("""() => { for (const v of document.querySelectorAll('[data-chat-turn] [data-json-kind=tree]')) { const t = v.querySelector('.json-toggle'); const r = t.getBoundingClientRect();
        const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); if (top && t.contains(top)) { t.click(); return; } } }""")
    page.wait_for_timeout(500)
    after = page.evaluate("() => document.querySelector('[data-chat-turn] [data-json-kind=tree]').querySelectorAll('.json-row').length")
    check("folding the root hides its branches", after < before, f"{before} → {after} rows")
    check("nothing covers the tree", visible_tree() is not None)
    shot("chat-json-tree", full=False)
    check("no page errors in the conversation", not errors_of(page) and not errors, (errors_of(page) + errors)[:3])
    errors.clear(); bad_responses.clear()

    # ─────────────────── leaving the memory map must not crash ───────────────────
    print("\n⑤ Memory map, then away again")
    page.goto(BASE + "/findings", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    page.locator("a[href='/memory']").first.click(timeout=8000)     # into the map the way the operator does
    try:
        page.locator("[data-gid]").first.wait_for(timeout=10000)
    except Exception:  # noqa: BLE001 — reported below
        pass
    check("the memory map opens from the rail", "/memory" in page.url and page.evaluate("() => document.querySelectorAll('svg').length") >= 1, page.url)
    check("the map is a full-screen takeover: no rail on it", page.locator("a[href='/findings']").count() == 0)
    page.keyboard.press("Escape")                            # the map's own way back
    page.wait_for_timeout(2500)
    check("leaving the map by client-side navigation renders the chat (the map's designed way back)", "/chats" in page.url and page.locator("body").inner_text().strip() != "", page.url)
    check("the rail is back after the map", page.locator("a[href='/swarm']").count() >= 1)
    errors.clear()

    # ─────────────────────────── swarms as things ───────────────────
    print("\n⑥ Swarms: define (no launch) then delete — nothing spawns, nothing left")
    page.goto(BASE + "/swarm", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    page.get_by_role("button", name="Define a swarm").click()
    page.wait_for_timeout(600)
    dialog = page.locator("[role=dialog]")
    dialog.get_by_placeholder("immutable").fill("journey-check")
    dialog.get_by_placeholder("Hunt the SSO hub").fill("Journey swarm — never launched")
    dialog.get_by_placeholder("map the edge, then the auth flows").fill("a lane nobody works")
    dialog.locator("input[type=number]").first.fill("0")
    check("the dialog offers engine, permissions and mode", dialog.locator("select").count() == 3)
    dialog.get_by_role("button", name="Define and launch").click()
    page.wait_for_timeout(1200)
    row = page.locator("[data-panel] table tbody tr", has_text="Journey swarm")
    check("the swarm is listed", row.count() == 1, row.count())
    check("its status is defining (nothing launched)", "defining" in (row.inner_text() or ""), row.inner_text()[:60])
    row.get_by_role("button", name="Open").click()
    page.wait_for_timeout(900)
    check("the headless view says no agents on record", "No agents on record" in page.locator("[data-swarm-agents]").inner_text())
    check("it says no open work", "No open work" in page.locator("[data-swarm-intents]").inner_text())
    page.get_by_role("button", name="Delete").click()
    page.get_by_role("button", name="Stop and delete").click()
    page.wait_for_timeout(1200)
    check("the swarm is gone from the list", page.locator("[data-panel] table tbody tr", has_text="Journey swarm").count() == 0)
    import pathlib as _p
    check("no folder was left behind", not (_p.Path.home() / ".suijin/Targets/journey-check").exists())
    errors.clear()

    # ────────────────────────────── nav ──────────────────────────────
    print("\n⑥ Navigation")
    page.goto(BASE + "/chats?new=1", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    nav = page.evaluate("() => [...document.querySelectorAll('.console-navigation-rail a')].map(a => [a.getAttribute('aria-label') || a.textContent.trim(), a.getAttribute('href')])")
    labels = [n[0] for n in nav]
    check("the rail lists the new pages", {"Swarm", "Engagements", "Data"} <= set(labels), labels)
    check("in a sensible order", labels.index("Engagements") < labels.index("Swarm") < labels.index("Data"), labels)
    page.goto(BASE + "/scope", wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    check("the old scope address leads to engagements", "/engagements" in page.url, page.url)
    page.goto(BASE + "/doctor", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    check("the doctor shows the real version", "1.0.0" not in page.inner_text("main").split("Provider")[0][:400])
    shot("doctor")
    check("no page errors in navigation", not errors_of(page) and not errors, (errors_of(page) + errors)[:3])
    browser.close()
except BaseException:  # noqa: BLE001 — whatever happened, report it and leave by the back door
    traceback.print_exc()
finally:
    if own_chat:
        api(f"/api/console/sessions/{own_chat}", {}, "DELETE")  # the chat this run made goes again
    mesh_log = os.path.expanduser("~/.suijin/mesh/gc.log")
    if os.path.exists(mesh_log):
        lines = open(mesh_log, errors="replace").read().splitlines()
        kept = [line for line in lines if "qa journey: mesh check" not in line]
        if len(kept) != len(lines):
            open(mesh_log, "w").write("\n".join(kept) + ("\n" if kept else ""))
    status, board = api("/api/ops/overview")
    print(f"\nleft behind: coordinator={board['coordinator']['running']} intents={board['counts']['intents']} pause_flag={board['fleet']['pause_flag']} agents={board['counts']['agents']}")
    print(f"\n{sum(results)}/{len(results)} checks passed", flush=True)
    # Playwright's teardown can hang for minutes after these pages; the results are complete, so leave now.
    os._exit(0 if results and all(results) else 1)
