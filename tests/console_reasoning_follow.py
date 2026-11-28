#!/usr/bin/env python3
"""The reasoning box, measured: it follows its own stream, and yields when the reader scrolls back.

Operator ruling 2026-11-28: "make the reasoning stream auto scroll as fast as it streams and user
can scroll back if needed". This spends ONE real chat turn on the leader model — it is a journey,
not a test, and pytest never collects it (its name is not test_*). Run against a console that is
already up, under a wall-clock limit of your own:

    python3 tests/console_reasoning_follow.py

It opens a chat mid-thought and samples the reasoning box every 150 ms: its scroll position while
the text grows, then after scrolling it to the top, then after returning it to the end. Three
RESULT lines and the raw samples go to server/web/.opencode/shots/reasoning-probe.log; the chat it
made is deleted at the end. The box is `.console-tool-reasoning` in console-ui/src/console/
tool-activity.tsx (ReasoningStream).
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8790"
OUT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server", "web", ".opencode", "shots")
)  # git-ignored; read screenshots from there
LOG = os.path.join(OUT, "reasoning-probe.log")
threading.Timer(240, lambda: os._exit(3)).start()
_log = open(LOG, "w")  # noqa: SIM115 — kept for the run, closed at the end


def say(*parts) -> None:
    print(" ".join(str(p) for p in parts), file=_log, flush=True)


def api(path, data=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=body,
        method=method or ("POST" if data is not None else "GET"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=40) as response:
        return json.loads(response.read().decode())


PROBE = """() => {
  const blocks = document.querySelectorAll('[data-reasoning]');
  const el = blocks[blocks.length - 1];
  if (!el) return null;
  return {len: el.innerText.length, top: Math.round(el.scrollTop), h: el.scrollHeight, c: el.clientHeight,
          following: el.hasAttribute('data-following'), blocks: blocks.length,
          smooth: getComputedStyle(el).scrollBehavior};
}"""
SET = "((y) => { const b = document.querySelectorAll('[data-reasoning]'); const el = b[b.length - 1]; el.scrollTop = %s; })()"

chat = api(
    "/api/console/sessions",
    {
        "title": "Think through, step by step and in detail, how you would review a password-reset flow for weaknesses",
        "kind": "chat",
    },
)["session"]
api(
    f"/api/console/sessions/{urllib.parse.quote(chat['id'])}/messages",
    {
        "text": "Think through, step by step and in careful detail, how you would review a password-reset flow for weaknesses: the request, the token, the page, the session after. Do not use any tool. Then answer in one short sentence."
    },
)
samples = []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        pg.goto(f"{BASE}/chats/{urllib.parse.quote(chat['id'])}", wait_until="domcontentloaded")
        phase, phase_at = "follow", None
        t0 = time.time()
        while time.time() - t0 < 150:
            probe = pg.evaluate(PROBE)
            now = round(time.time() - t0, 2)
            if probe and probe["len"]:
                samples.append((now, phase, probe))
            if (
                phase == "follow"
                and probe
                and probe["h"] > probe["c"] + 12
                and len([s for s in samples if s[1] == "follow"]) >= 8
                and probe["following"]
            ):
                phase, phase_at = "back", now
                pg.evaluate(SET % "0")
                pg.screenshot(path=os.path.join(OUT, "reasoning-probe-1-scrolled-back.png"), timeout=15000)
            elif phase == "back" and now - phase_at > 4:
                phase, phase_at = "follow-again", now
                pg.evaluate(SET % "el.scrollHeight")
            elif phase == "follow-again" and now - phase_at > 4:
                break
            pg.wait_for_timeout(150)
        pg.screenshot(path=os.path.join(OUT, "reasoning-probe-2-after.png"), timeout=15000)
finally:
    api(f"/api/console/sessions/{urllib.parse.quote(chat['id'])}", data={}, method="DELETE")


def show(label, rows, limit=60):
    say(f"--- {label}: {len(rows)} samples")
    last_len = None
    for now, phase, s in rows[:limit]:
        mark = ""
        if last_len is not None and s["len"] != last_len:
            mark = "grew" if s["len"] > last_len else "SHRANK"
        at_end = s["top"] + s["c"] >= s["h"] - 3
        say(
            f"  {now:6.2f}s {phase:11s} len={s['len']:5d} top={s['top']:4d} c={s['c']:3d} h={s['h']:4d} {'END ' if at_end else '    '} following={int(s['following'])} blocks={s['blocks']} {mark}"
        )
        last_len = s["len"]


say("scroll-behavior of the box:", samples[0][2]["smooth"] if samples else None)
for name in ("follow", "back", "follow-again"):
    show(name, [s for s in samples if s[1] == name])
back = [s for s in samples if s[1] == "back"]
if len(back) >= 2:
    grew = len({s[2]["len"] for s in back}) > 1
    stayed = all(s[2]["top"] <= 2 for s in back[1:] if s[2]["len"] > back[0][2]["len"])
    say(f"RESULT scrolled back: text grew {grew}; the reader stayed where they put it: {stayed}")
again = [s for s in samples if s[1] == "follow-again"]
if again:
    say(
        f"RESULT back at the end: at the end in {sum(1 for s in again if s[2]['top'] + s[2]['c'] >= s[2]['h'] - 3)}/{len(again)} samples"
    )
follow = [s for s in samples if s[1] == "follow" and s[2]["h"] > s[2]["c"] + 12]
if follow:
    say(
        f"RESULT while following (once the box overflowed): at the end in {sum(1 for s in follow if s[2]['top'] + s[2]['c'] >= s[2]['h'] - 3)}/{len(follow)} samples"
    )
_log.close()
os._exit(0)
