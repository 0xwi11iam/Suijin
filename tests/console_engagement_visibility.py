#!/usr/bin/env python3
"""End to end, through the real doors: a chat starts an engagement, and everything about that
engagement is visible and controllable — the way the operator does it.

  1. the hero shows Sui, the sidebar the newer mark
  2. a chat is asked to start an engagement; its reasoning streams in light grey
  3. the engagement's journal fills in order: mission, state, reasoning, call, result
  4. the real "Pause agents" button pauses; a typed message while paused resumes the agent
  5. the chat sits in the engagement's folder; nothing is left behind

Operator rulings 2026-11-28: "even when engagements are spawned, not by myself, it's still
everything visible in the ui"; "ensure that button actually pauses and resends". This spends one
real chat turn on the leader and one throwaway engagement on the worker model (a local-only
mission, no network) — it is a journey, not a test, and pytest never collects it. Run against a
console that is already up:

    python3 tests/console_engagement_visibility.py

Everything it creates is deleted at the end. The report goes to server/web/.opencode/shots/
verify-visibility.log, the screenshots beside it. (The reasoning box's scrolling has its own
probe: tests/console_reasoning_follow.py.)
"""

import contextlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8790"
OUT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server", "web", ".opencode", "shots")
)  # git-ignored; read screenshots from there
LOG = os.path.join(OUT, "verify-visibility.log")
os.makedirs(OUT, exist_ok=True)
threading.Timer(540, lambda: os._exit(3)).start()  # the only reliable overall limit

_log = open(LOG, "w")  # noqa: SIM115 — kept for the run, closed in finish()
results: list[tuple[str, bool, str]] = []


def say(*parts) -> None:
    line = " ".join(str(p) for p in parts)
    print(line, file=_log, flush=True)


def check(name: str, ok: bool, detail="") -> bool:
    results.append((name, bool(ok), str(detail)[:300]))
    say(("  PASS  " if ok else "  FAIL  ") + name + (f"  — {str(detail)[:300]}" if detail != "" else ""))
    return bool(ok)


def api(path: str, data=None, method=None, timeout=30):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=body,
        method=method or ("POST" if data is not None else "GET"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode())


def wait_until(predicate, timeout: float, interval: float = 1.0, what: str = ""):
    """Poll without Playwright: safe from the main thread between browser calls."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = predicate()
        except Exception as error:  # noqa: BLE001 — a session that does not exist yet
            last = f"{type(error).__name__}: {error}"
        if last and not isinstance(last, str):
            return last
        time.sleep(interval)
    say(f"        (gave up waiting for {what or 'it'} after {timeout:.0f}s; last: {str(last)[:160]})")
    return None


def journal(session: str) -> list[dict]:
    """The engagement's records as the Engagements page lists them."""
    try:
        page = api(f"/api/ops/engagements/{urllib.parse.quote(session)}/events?offset=0&limit=400", timeout=20)
    except Exception:  # noqa: BLE001 — it may not exist yet
        return []
    rows = (
        [row for row in (page.get("rows") or page.get("records") or []) if isinstance(row, dict)]
        if isinstance(page, dict)
        else []
    )
    return list(reversed(rows))  # the page lists the newest first; a journal reads from its beginning


REASONING_PROBE = """() => {
  const blocks = document.querySelectorAll('[data-reasoning]');
  const el = blocks[blocks.length - 1];  // the newest: the one that is streaming
  if (!el) return null;
  const cs = getComputedStyle(el);
  return {len: el.innerText.length, top: el.scrollTop, h: el.scrollHeight, c: el.clientHeight,
          color: cs.color, following: el.hasAttribute('data-following'),
          answer: (document.querySelector('[data-chat-turn] .console-markdown:not([data-streaming])') || {}).innerText?.length ?? 0};
}"""


def rgb(color: str):
    """'color(srgb r g b / a)', 'rgb(r, g, b)' or 'oklab(L a b / a)' → (lightness 0..1, neutrality 0..1)."""
    nums = [float(x) for x in re.findall(r"-?\d+\.?\d*(?:e-?\d+)?", color.split("(")[1])]
    if color.startswith("oklab("):
        return nums[0], 1 - min(1.0, max(abs(nums[1]), abs(nums[2])) * 20)
    if color.startswith("color(srgb"):
        r, g, b = nums[:3]
        return (r + g + b) / 3, 1 - min(1.0, (max(r, g, b) - min(r, g, b)) * 10)
    r, g, b = nums[:3]
    return (r + g + b) / 765, 1 - min(1.0, (max(r, g, b) - min(r, g, b)) / 25.5)


def main() -> int:
    import urllib.parse

    before = {s["id"] for s in api("/api/console/sessions")["sessions"]}
    say(f"== started {time.strftime('%H:%M:%S')}; sessions before: {len(before)}")
    created: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            pg = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)[:160]))

            # ── 1. the marks ────────────────────────────────────────────────────────────────────
            say("\n① the marks")
            pg.goto(BASE + "/chats?new=1", wait_until="domcontentloaded")
            # The hero lives in its own frame. Ask the frame for the mark — a real call, which is also
            # what lets Playwright's synchronous connection deliver its events (a loop that only reads
            # page.frames between sleeps never learns that a frame attached).
            hero = pg.frame_locator("[data-suijin-hero-frame]")
            mark = hero.locator("[data-suijin-home-brand] img")
            mark.wait_for(timeout=20000)
            pg.wait_for_timeout(1500)
            box, src = mark.bounding_box(), (mark.get_attribute("src") or "")
            import base64

            inline = base64.b64decode(src.split(",", 1)[1]) if src.startswith("data:image/png;base64,") else b""
            with open(os.path.expanduser("~/suijin/server/web/console-ui/src/assets/sui-mark.png"), "rb") as handle:
                check("the hero shows Sui, the old pixel mark", inline == handle.read() and box, f"{src[:30]}… {box}")
            check(
                "drawn 40×36 — two pixels a block",
                box and abs(box["width"] - 40) < 1 and abs(box["height"] - 36) < 1,
                box,
            )
            rendering = mark.evaluate("el => getComputedStyle(el).imageRendering")
            check("without smoothing", rendering in ("pixelated", "crisp-edges"), rendering)
            pg.screenshot(path=os.path.join(OUT, "verify-01-hero.png"), timeout=15000)
            rail = pg.evaluate(
                "[...document.querySelectorAll('.console-navigation-rail img')].map(i => i.getAttribute('src'))"
            )
            check(
                "the sidebar keeps the newer mark", bool(rail) and all("sui-mark" not in (s or "") for s in rail), rail
            )

            # ── 2. a chat starts an engagement ────────────────────────────────────────────────
            say("\n② a chat is asked to start an engagement")
            ask = (
                "Use the start_engagement tool now. Mission, exactly: 'Run the local tool local_sys_info once and "
                "report what it says, then wait for the operator. Do not use the network.' Set target to 'Local check', "
                "permissions to recon, mode to vdp. Then tell me it started."
            )
            chat = api("/api/console/sessions", {"title": ask, "kind": "chat"})["session"]
            created.append(chat["id"])
            check(
                "the chat's title is short, not the whole prompt",
                len(chat["title"]) <= 64 and chat["title"] != ask,
                repr(chat["title"]),
            )
            api(f"/api/console/sessions/{urllib.parse.quote(chat['id'])}/messages", {"text": ask})
            pg.goto(f"{BASE}/chats/{urllib.parse.quote(chat['id'])}", wait_until="domcontentloaded")
            pg.locator("[data-chat-turn], [data-reasoning]").first.wait_for(timeout=20000)

            samples = []
            deadline = time.time() + 150
            while time.time() < deadline:
                probe = pg.evaluate(REASONING_PROBE)
                if probe:
                    samples.append(probe)
                engagement = next(
                    (
                        s
                        for s in api("/api/console/sessions")["sessions"]
                        if s["id"] not in before and s.get("kind") == "engagement"
                    ),
                    None,
                )
                if engagement and len(samples) >= 8:
                    break
                pg.wait_for_timeout(280)
            lengths = {s["len"] for s in samples if s["len"]}
            check(
                "the chat's reasoning streamed (it grew over time)",
                len(lengths) >= 6,
                f"{len(samples)} samples, {len(lengths)} distinct lengths",
            )
            last = next((s for s in reversed(samples) if s["len"]), None)
            light, neutral = rgb(last["color"]) if last else (0, 0)
            answer_color = pg.evaluate(
                "(() => { const el = [...document.querySelectorAll('[data-chat-turn] .console-markdown')].find(e => !e.closest('[data-reasoning]')); return el ? getComputedStyle(el).color : getComputedStyle(document.body).color; })()"
            )
            check(
                "the reasoning is light grey",
                last and 0.55 <= light <= 0.9 and neutral >= 0.95,
                f"{last['color'] if last else None} → lightness {light:.2f}, neutrality {neutral:.2f}",
            )
            check(
                "and not the colour of the answer",
                last and last["color"] != answer_color,
                f"{last['color'] if last else None} vs {answer_color}",
            )

            engagement = wait_until(
                lambda: next(
                    (
                        s
                        for s in api("/api/console/sessions")["sessions"]
                        if s["id"] not in before and s.get("kind") == "engagement"
                    ),
                    None,
                ),
                90,
                1.5,
                "the engagement the chat starts",
            )
            if not check("the chat started an engagement", engagement, engagement and engagement["id"]):
                return finish(created, errors)
            eng = engagement["id"]
            created.append(eng)
            say(
                f"        engagement: {eng[:48]}…  target {engagement.get('targetLabel')!r}  title {engagement.get('title')!r}"
            )
            check(
                "the engagement has the target the chat gave",
                engagement.get("targetLabel") == "Local check",
                engagement.get("targetLabel"),
            )
            chat_row = next(s for s in api("/api/console/sessions")["sessions"] if s["id"] == chat["id"])
            check(
                "the chat now files in the engagement's folder",
                chat_row.get("targetKey") == engagement.get("targetKey") == "local check",
                chat_row.get("targetKey"),
            )

            # ── 3. the engagement's journal, in order ────────────────────────────────────────
            say("\n③ what the engagement shows")
            pg.goto(f"{BASE}/chats/{urllib.parse.quote(eng)}", wait_until="domcontentloaded")
            # (The reasoning box's scrolling is measured by its own probe, console_reasoning_follow.py,
            # which samples while the reader is scrolled back.)
            wait_until(
                lambda: (lambda recs: recs if any(r.get("kind") == "tool.result" for r in recs) else None)(
                    journal(eng)
                ),
                150,
                1.5,
                "the agent's first tool result",
            )
            recs = journal(eng)
            pg.wait_for_timeout(1200)
            reasoning = pg.evaluate(REASONING_PROBE)  # the newest reasoning block on the page
            if reasoning:
                grey = rgb(reasoning["color"])
                check(
                    "an agent's reasoning is the same light grey",
                    0.55 <= grey[0] <= 0.9 and grey[1] >= 0.95,
                    reasoning["color"],
                )
            pg.screenshot(path=os.path.join(OUT, "verify-02b-engagement-reasoning.png"), timeout=15000)
            kinds = [str(r.get("kind")) for r in recs]
            say(
                "        journal, in order:",
                " → ".join(k for n, k in enumerate(kinds) if n == 0 or kinds[n - 1] != k)[:600],
            )
            check(
                "the mission is the first record",
                kinds[:1] == ["user.message"] and "local_sys_info" in str(recs[0].get("content")),
                kinds[:1],
            )
            check(
                "the agent said it was working",
                "phase.transition" in kinds,
                [k for k in kinds if k == "phase.transition"],
            )
            check(
                "its reasoning is on the record",
                "assistant.reasoning" in kinds or any(r.get("phase") == "thought" for r in recs),
                kinds.count("assistant.reasoning"),
            )
            check(
                "and its tool calls, with results",
                kinds.count("tool.call") >= 1 and kinds.count("tool.result") >= 1,
                f"{kinds.count('tool.call')} calls, {kinds.count('tool.result')} results",
            )
            check(
                "a call is on the record before its result",
                kinds.index("tool.call") < kinds.index("tool.result")
                if "tool.call" in kinds and "tool.result" in kinds
                else False,
            )
            roster = api(f"/api/console/sessions/{urllib.parse.quote(eng)}")["snapshot"]["workers"]
            worker = roster[0] if roster else {}
            turns = worker.get("transcript") or []
            check(
                "the roster carries the agent's transcript",
                any(turn.get("tools") for turn in turns)
                and any(turn.get("assistant") or turn.get("reasoning_summary") for turn in turns),
                f"{len(turns)} turns; the last: {json.dumps(turns[-1] if turns else None)[:200]}",
            )
            check("and the model it thinks with", bool(worker.get("model")), worker.get("model"))
            pg.wait_for_timeout(1200)
            tree = (
                pg.locator("[data-agent-status]").first.inner_text()
                if pg.locator("[data-agent-status]").count()
                else ""
            )
            check(
                "the agents tree says what it is doing (not 'Thinking…' forever)",
                bool(tree) and any(w in tree.lower() for w in ("local", "sys", "using", "pass", "wait", "finished")),
                repr(tree[:200]),
            )
            pg.screenshot(path=os.path.join(OUT, "verify-03-engagement-working.png"), timeout=15000)
            say("        agents tree:", repr(tree[:240]))

            # ── 4. pause with the real button; a typed message resumes ───────────────────────
            say("\n④ pause, then a message")
            # Pause while there is something to pause: an agent that already ran out of work shows no
            # Pause button (nothing is running) — a message revives it first.
            pause = pg.get_by_role("button", name="Pause agents", exact=True)
            if not pause.count():
                say("        (the agent had already run out of work; a message revives it before pausing)")
                api(
                    f"/api/console/sessions/{urllib.parse.quote(eng)}/messages",
                    {"text": "Keep going: run local_sys_info once more, then wait."},
                )
                wait_until(
                    lambda: pg.get_by_role("button", name="Pause agents", exact=True).count() > 0,
                    60,
                    1.0,
                    "the agent working again",
                )
            paused_ok = False
            for _ in range(30):
                if pg.get_by_role("button", name="Resume agents", exact=True).count():
                    paused_ok = True
                    break
                if pause.count() and pause.first.is_visible():
                    with contextlib.suppress(Exception):  # it may have changed under the click
                        pause.first.click(timeout=4000)
                pg.wait_for_timeout(1500)
            check("the Pause agents button pauses the agent (Resume offered)", paused_ok)
            held = wait_until(
                lambda: (lambda recs: recs if any(r.get("to") == "paused" for r in recs) else None)(journal(eng)),
                60,
                1.0,
                "the agent's own 'paused' record",
            )
            check("the agent itself says it is holding", bool(held))
            pg.wait_for_timeout(1200)
            pg.screenshot(path=os.path.join(OUT, "verify-04-engagement-paused.png"), timeout=15000)
            results_before = len([r for r in journal(eng) if r.get("kind") == "tool.result"])
            note = f"Message from the operator: run local_sys_info once more, then wait. ({int(time.time())})"
            box = pg.get_by_label("Message Suijin")
            box.click(timeout=8000)
            box.fill(note)
            box.press("Enter")
            delivered = wait_until(
                lambda: (lambda recs: recs if any(r.get("kind") == "guidance.delivered" for r in recs) else None)(
                    journal(eng)
                ),
                40,
                1.0,
                "the message reaching the agent",
            )
            check("a message typed while paused reaches the agent", bool(delivered))
            again = wait_until(
                lambda: (lambda n: n if n > results_before else None)(
                    len([r for r in journal(eng) if r.get("kind") == "tool.result"])
                ),
                180,
                2.0,
                "the agent working again",
            )
            check(
                "and the agent resumes and works",
                bool(again),
                f"tool results {results_before} → {len([r for r in journal(eng) if r.get('kind') == 'tool.result'])}",
            )
            snapshot = api(f"/api/console/sessions/{urllib.parse.quote(eng)}")["snapshot"]
            check(
                "the session is no longer paused",
                snapshot["session"].get("paused") is False,
                snapshot["session"].get("status"),
            )
            pg.wait_for_timeout(1500)
            pg.screenshot(path=os.path.join(OUT, "verify-05-after-resume.png"), timeout=15000)

            # ── 5. the folder ─────────────────────────────────────────────────────────────────
            say("\n⑤ the sidebar")
            rail_text = pg.locator(".console-navigation-rail").inner_text()
            position = rail_text.find("Local check")
            check(
                "one folder holds the engagement and the chat that started it",
                position >= 0 and "Local check" not in rail_text[position + 1 :],
                repr(rail_text[position : position + 260]),
            )
            check("no page error during any of it", not errors, errors[:3])
            return finish(created, errors)
    except Exception as error:  # noqa: BLE001
        say(f"!! {type(error).__name__}: {str(error)[:400]}")
        return finish(created, [])


def finish(created: list[str], errors: list[str]) -> int:
    import urllib.parse

    say("\n⑥ cleanup")
    for session in created:
        try:
            api(f"/api/console/sessions/{urllib.parse.quote(session)}", data={}, method="DELETE", timeout=60)
        except Exception as error:  # noqa: BLE001
            say(f"        could not delete {session[:30]}…: {type(error).__name__}: {str(error)[:120]}")
    left = [s["id"] for s in api("/api/console/sessions")["sessions"] if s["id"] in created]
    check("everything this run created is deleted", not left, left)
    passed = sum(1 for _name, ok, _detail in results if ok)
    say(f"\n{passed}/{len(results)} checks passed")
    _log.close()
    os._exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
    os._exit(2)
