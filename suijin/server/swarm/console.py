"""swarm console — the live dashboard (advanced mode).

Design contract follows the box tui (operator rulings): lowercase
everywhere, ascii glyphs, color carries meaning, clean at 80 cols.
Layout (the approved ascii mock, 2026-11-06):

  +----------------+---------------+
  | system         | agents        |
  +----------------+---------------+
  | intents                        |
  +--------------------------------+
  | findings — the honest board    |
  +--------------------------------+
  | details — selected agent       |
  +--------------------------------+
  | footer: keys                   |

Keys: up/down select agent · s steer selected · c copy details
(osc52 → works over ssh) · k kill selected · r force refresh · q quit.

Everything reads from swarm.db + agent logs — no polling of the bus,
no hidden state. Dead agents drop off after the heartbeat timeout.
"""

from __future__ import annotations

import contextlib
import curses
import os
import re
import sqlite3
import subprocess
import time
import sys
from pathlib import Path

from suijin.server.swarm.coordinator import swarm_db_path

REFRESH_S = 2.0
HB_DEAD_S = 30  # heartbeat age after which an agent is marked dead


# ── data ─────────────────────────────────────────────────────────


def _db_rows():
    """(agents, intents, findings, facts_n) — suppressed, the console
    must never crash on a locked/corrupt db."""
    agents, intents, findings, facts_n = [], [], [], 0
    with contextlib.suppress(Exception):
        db = sqlite3.connect(str(swarm_db_path()), timeout=3)
        db.execute("PRAGMA journal_mode=DELETE")  # mode rides the file; harmless read-side noop
        now = time.time()
        # THE LANE TRUTH: running intents first (immediate — no agent
        # restart needed), then the agent's own current_intent (mission
        # mode). Never 'idle' for a live agent (operator field report).
        lanes = {}
        for r in db.execute(
            "SELECT agent_id, text FROM intents WHERE state = 'running'"
        ).fetchall():
            if r[0]:
                lanes[r[0]] = (r[1] or "")[:110]
        for r in db.execute(
            "SELECT id, current_intent, last_heartbeat FROM agents ORDER BY last_heartbeat DESC"
        ).fetchall():
            lane = lanes.get(r[0]) or r[1] or ""
            agents.append({"id": r[0], "intent": lane, "hb_age": int(now - (r[2] or 0))})
        for r in db.execute(
            "SELECT state, COUNT(*) FROM intents GROUP BY state"
        ).fetchall():
            intents.append((r[0], r[1]))
        for r in db.execute(
            "SELECT severity, status, title FROM findings ORDER BY created_at DESC LIMIT 6"
        ).fetchall():
            findings.append({"severity": r[0] or "?", "status": r[1] or "?", "title": (r[2] or "")[:44]})
        facts_n = db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        db.close()
    return agents, intents, findings, facts_n


def _detail_events(agent_id: str, limit: int = 3) -> list[dict]:
    """The selected agent's recent turns from events.jsonl — NATIVE
    event_log records (tool.call/tool.result paired by id, assistant
    .message for thought), filtered by the agent field. Same file for
    host and boxed agents (boxed agents write into the mounted
    engagement dir)."""
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

        ev = Path(WORKSPACE_DIR).parent / "engagements" / "_default" / "events.jsonl"
        if not ev.exists():
            return []
        calls: dict = {}
        thought_by_seq: dict = {}
        order: list = []
        for ln in ev.read_text(errors="replace").splitlines()[-200:]:
            with contextlib.suppress(Exception):
                import json as _json

                e = _json.loads(ln)
                if e.get("agent") != agent_id:
                    continue
                k = e.get("kind")
                if k == "assistant.message":
                    thought_by_seq[int(e.get("seq") or 0)] = str(e.get("content") or "")
                elif k == "tool.call":
                    tid = str(e.get("id"))
                    calls[tid] = {"tool": e.get("name"), "args": e.get("args") or {},
                                  "seq": int(e.get("seq") or 0), "obs": ""}
                    order.append(tid)
                elif k == "tool.result":
                    c = calls.get(str(e.get("id")))
                    if c is not None:
                        c["obs"] = str(e.get("output") or "")
        out = []
        for tid in order[-limit:]:
            c = calls[tid]
            out.append({"tool": c["tool"], "args": c["args"], "obs": c["obs"],
                        "thought": thought_by_seq.get(c["seq"], "")})
        return out
    return []


def _prettify_jsonish(text: str, width: int = 74) -> str:
    """JSON prettification is ALWAYS present (operator directive): any
    embedded {...} in displayed text gets re-indented instead of riding
    raw on one line. Non-JSON text passes through untouched."""
    import json as _json
    import re as _re

    def _fmt(m):
        with contextlib.suppress(Exception):
            obj = _json.loads(m.group(0))
            pretty = _json.dumps(obj, indent=1, ensure_ascii=False)
            return pretty[: 3 * width]
        return m.group(0)

    return _re.sub(r"\{.*\}", _fmt, str(text or ""))[: 3 * width]


def _render_detail_pane(stdscr, y: int, maxx: int, agent_id: str, events: list[dict], log_lines: list[str]) -> None:
    """THE OPERATOR CONSOLE'S DETAILS PANEL, ported: thought → tool+args
    (values capped) → observation (capped, separated). Falls back to the
    log lines when the agent hasn't emitted events yet."""
    row = y
    if events:
        for e in events:
            thought = str(e.get("thought") or "")
            act = e.get("action") or {}
            tool = str(act.get("tool") or "?")
            args = act.get("args") or {}
            obs = str(e.get("observation") or "")
            if thought:
                _line(stdscr, row, 2, f"  | thought: {_prettify_jsonish(' '.join(thought.split()))[:76]}", curses.color_pair(4))
                row += 1
            _line(stdscr, row, 2, f"  > {tool}", curses.color_pair(1) | curses.A_BOLD)
            row += 1
            for k in sorted(args.keys())[:4]:
                v = str(args[k])
                if len(v) > 56:
                    v = v[:53] + "…"
                _line(stdscr, row, 6, f"{k}: {v}", curses.A_DIM)
                row += 1
            if obs:
                _line(stdscr, row, 2, "  " + "-" * min(40, maxx - 6), curses.A_DIM)
                row += 1
                _line(stdscr, row, 2, f"  {_prettify_jsonish(obs)[:150]}", curses.A_DIM)
                row += 1
            row += 1
    else:
        for ln in log_lines:
            _line(stdscr, row, 2, f"  {ln[:76]}", curses.A_DIM)
            row += 1


def _tool_log(agent_id: str, limit: int = 12) -> list[str]:
    """The selected agent's recent tool calls. Process agents log to
    tmp files; boxed agents log to `docker logs` — both are honest
    sources, the console reads whichever the agent lives in."""
    import tempfile

    p = Path(tempfile.gettempdir()) / f"suijin-agent-{agent_id}.log"
    if p.exists():
        out: list[str] = []
        with contextlib.suppress(Exception):
            lines = p.read_text(errors="replace").splitlines()
            for ln in lines[-60:]:
                m = re.match(rf"\[{re.escape(agent_id)}\] (turn \d+: .*|steering: .*|claimed: .*)", ln)
                if m:
                    out.append(m.group(1)[:70])
        if out:
            return out[-limit:]
    if agent_id.endswith("-sbx") or "-sbx" in agent_id:
        with contextlib.suppress(Exception):
            r = subprocess.run(
                ["docker", "logs", "--tail", str(limit * 2), f"suijin-sbx-default-{agent_id}"],
                capture_output=True, text=True, timeout=15,
            )
            lines = []
            for ln in (r.stdout + r.stderr).splitlines():
                m = re.match(rf"\[{re.escape(agent_id)}\] (turn \d+: .*|steering: .*|claimed: .*|done: .*|blocked: .*|exhausted: .*)", ln)
                if m:
                    lines.append(m.group(1)[:70])
            if lines:
                return lines[-limit:]
            return ["(box running, no tool calls yet)"]
    return ["(no log — not a process agent?)"]


def _sys_info():
    import shutil

    cpu_s = mem_s = "n/a"
    engine = "n/a"
    try:
        import psutil

        cpu = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        cpu_s = f"{cpu:.0f}%"
        mem_s = f"{vm.used / 1e9:.1f} / {vm.total / 1e9:.1f} gb"
    except Exception:  # noqa: BLE001
        pass
    with contextlib.suppress(Exception):
        from suijin.server.box.engine import detect_engine, _engine_binary

        engine = _engine_binary(detect_engine(interactive=False))
        if shutil.which("orbctl") and engine == "orb":
            engine = "orbstack"
    return {"cpu": cpu_s, "mem": mem_s, "engine": engine}


def _pool_count() -> int:
    with contextlib.suppress(Exception):
        from suijin.server.box.hypervisor import pool_members

        return len(pool_members())
    return 0


# ── actions (the keys that DO something) ─────────────────────────


def _copy_osc52(text: str) -> None:
    """Clipboard copy via OSC52 — survives ssh; local terminals that
    honor it also work. Best-effort, never raises."""
    import base64

    b64 = base64.b64encode(text.encode()).decode()
    with contextlib.suppress(Exception):
        sys.stdout.write(f"\x1b]52;c;{b64}\x07")
        sys.stdout.flush()
    with contextlib.suppress(Exception):
        subprocess.run(["pbcopy"], input=text.encode(), check=False, timeout=5)


def _steer(agent_id: str, stdscr, message: str) -> None:
    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import BusClient
        from suijin.server.swarm.lifecycle import _bus_addr

        addr = _bus_addr()
        if not addr:
            return
        bc = BusClient()
        if bc.connect(addr[0], addr[1], f"console-{os.urandom(2).hex()}"):
            bc.publish(f"/agent/{agent_id}/steer", {"message": message})
            bc.close()


def _kill(agent_id: str) -> None:
    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import BusClient
        from suijin.server.swarm.lifecycle import _bus_addr

        addr = _bus_addr()
        if not addr:
            return
        bc = BusClient()
        if bc.connect(addr[0], addr[1], f"console-{os.urandom(2).hex()}"):
            bc.publish(f"/agent/{agent_id}/kill", {"reason": "console kill"})
            bc.close()


def _prompt(stdscr, y: int, label: str) -> str:
    curses.echo()
    stdscr.addstr(y, 2, label)
    stdscr.refresh()
    try:
        val = stdscr.getstr(y, 2 + len(label), 200).decode(errors="replace").strip()
    except Exception:  # noqa: BLE001
        val = ""
    curses.noecho()
    return val


# ── the screen ───────────────────────────────────────────────────


def run_console(stdscr, poll: float = REFRESH_S) -> int:
    curses.curs_set(0)
    stdscr.nodelay(True)
    curses.start_color()
    curses.use_default_colors()
    for i, fg in ((1, curses.COLOR_GREEN), (2, curses.COLOR_YELLOW), (3, curses.COLOR_RED),
                  (4, curses.COLOR_CYAN), (5, curses.COLOR_MAGENTA)):
        curses.init_pair(i, fg, -1)

    sel = 0
    last_agents: list = []
    details_for = ""
    details_cache: list = []
    last_copy_msg = ""
    last_copy_t = 0.0

    while True:
        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), 27):
            return 0
        agents, intents, findings, facts_n = _db_rows()
        live_n = sum(1 for a in agents if a["hb_age"] <= HB_DEAD_S)
        # DEAD AGENTS STAY VISIBLE (red) — an operator wants to see
        # them, not have them vanish (first render hid them entirely)
        sel = min(sel, max(0, len(agents) - 1))
        cur_id = agents[sel]["id"] if agents else ""

        if key == curses.KEY_DOWN:
            sel = min(sel + 1, max(0, len(agents) - 1))
        elif key == curses.KEY_UP:
            sel = max(sel - 1, 0)
        elif key in (ord("s"), ord("S")) and agents:
            msg = _prompt(stdscr, curses.LINES - 2, " steer selected: ")
            if msg:
                _steer(cur_id, stdscr, msg)
        elif key in (ord("k"), ord("K")) and agents:
            _kill(cur_id)
        elif key in (curses.KEY_ENTER, 10, ord("v"), ord("V"), ord("o"), ord("O")) and agents:
            # OPEN THE AGENT'S LIVE VIEW (attach) from inside the console
            with contextlib.suppress(Exception):
                curses.endwin()
                aid = agents[min(sel, len(agents) - 1)]["id"]
                subprocess.call(["suijin", "swarm", "attach", aid], stdin=None)
                curses.doupdate()
                stdscr.refresh()
                curses.curs_set(0)
                stdscr.nodelay(True)
        elif key in (ord("c"), ord("C")) and agents:
            _copy_osc52("\n".join(details_cache))
            last_copy_msg = f"details of {cur_id} copied"
            last_copy_t = time.time()

        if cur_id != details_for:
            details_for = cur_id
            details_events = _detail_events(cur_id)
            details_cache = _tool_log(cur_id)

        h, w = stdscr.getch, stdscr.getmaxyx
        stdscr.erase()
        maxy, maxx = stdscr.getmaxyx()
        mid_x = max(24, min(40, maxx // 2))

        sysd = _sys_info()
        pool = _pool_count()
        open_n = sum(n for s, n in intents if s == "open")
        run_n = sum(n for s, n in intents if s == "running")
        done_n = sum(n for s, n in intents if s in ("done", "exhausted", "blocked"))

        def line(y, x, txt, attr=0):
            with contextlib.suppress(Exception):
                stdscr.addstr(y, x, txt[: max(1, maxx - x - 1)], attr)

        # header
        line(0, 2, " suijin swarm console ", curses.color_pair(4) | curses.A_REVERSE | curses.A_BOLD)

        # system pane (left)
        y = 2
        line(y, 2, "system", curses.A_UNDERLINE)
        line(y + 1, 2, f"cpu  {sysd['cpu']}")
        line(y + 2, 2, f"mem  {sysd['mem']}")
        line(y + 3, 2, f"engine  {sysd['engine']}")
        line(y + 4, 2, f"warm pile  {pool} box(es), no agents")
        line(y + 5, 2, f"facts  {facts_n}")

        # agents pane (right of system) — ALL visible, dead = red
        ax = mid_x + 2
        line(y, ax, f"agents ({live_n} live / {len(agents)} known)", curses.A_UNDERLINE)
        for i, a in enumerate(agents[: max(1, maxy - y - 8)]):
            cur_y = y + 1 + i
            sel_mark = ">" if i == sel else " "
            dead = a["hb_age"] > HB_DEAD_S
            color = curses.color_pair(3) if dead else curses.color_pair(1)
            attr = color | (curses.A_BOLD if i == sel else 0)
            # THE TITLE, BOLD — the lane is the story, not decoration
            lane = (a["intent"] or "live")[:24]
            line(cur_y, ax, f"{sel_mark}{a['id'][:26]:26s}", attr)
            line(cur_y, ax + 28, lane, curses.A_BOLD)
            line(cur_y, ax + 54, f"hb {a['hb_age']}s", curses.color_pair(3) if dead else curses.A_DIM)

        # intents row
        iy = max(y + 8, len(agents) + 3)
        line(iy, 2, f"intents: {open_n} open · {run_n} running · {done_n} closed", curses.A_BOLD)

        # findings — the honest board
        fy = iy + 1
        line(fy, 2, "findings — the honest board", curses.A_UNDERLINE)
        sev_pair = {"critical": 3, "high": 3, "medium": 2, "low": 4, "info": 5}
        if not findings:
            line(fy + 1, 2, "  none yet", curses.A_DIM)
        else:
            for i, f in enumerate(findings[: max(1, maxy - fy - 6)]):
                pair = curses.color_pair(sev_pair.get(f["severity"].lower(), 0))
                line(fy + 1 + i, 2, f"  {f['severity']:8s} {f['status']:12s} {f['title']}", pair)

        # details pane (bottom): selected agent's recent calls
        dy = max(maxy - (len(details_cache) + 3), fy + len(findings) + 3)
        line(dy - 1, 2, "-" * max(10, maxx - 4), curses.A_DIM)
        line(dy, 2, f"details — {details_for}   [Enter] attach  [c] copy  [s] steer  [k] kill", curses.A_UNDERLINE)
        _render_detail_pane(stdscr, dy + 1, maxx, details_for, details_events, details_cache)

        # footer
        msg = ""
        if last_copy_msg and time.time() - last_copy_t < 4:
            msg = f" {last_copy_msg}"
        line(maxy - 1, 2, f" up/down select · s steer · k kill · c copy · r refresh · q quit{msg}",
             curses.A_REVERSE)

        stdscr.refresh()
        time.sleep(0.12 if key != -1 else poll)


def run_console_cmd() -> int:
    """`suijin swarm console` — curses wrapper + engine note."""
    if os.environ.get("SUIJIN_CONSOLE_NO_TTY") != "1" and not sys.stdout.isatty():
        print("swarm console needs a terminal (tty). pipe mode is not supported.")
        return 1
    return curses.wrapper(run_console)
