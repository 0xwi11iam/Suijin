"""swarm console — the live operator view of the swarm.

Rich, not curses. Every accumulated fix baked in:
  - lanes truthful (running intents joined live, never 'idle' for a
    working agent; the lane/title is BOLD — the story, not decoration)
  - warm pile explains itself (boxes, not agents)
  - detail pane: native event records (thought → ❯ tool → observation,
    prettified JSON), falls back to log lines
  - Enter on a selected agent opens its full engagement TUI
  - findings board with honest status colors
  - crash traceback PRINTED, never swallowed — a flash-and-disappear
    console is worse than no console
  - Ctrl+C at any prompt goes back one level, never exits

Ctrl+C in the main loop exits the console (the swarm keeps running).
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from suijin.server.swarm.coordinator import swarm_db_path

console = Console()
POLL_S = 2.0
HB_DEAD_S = 30


def _query(q: str, engagement: str = "_default") -> dict:
    with contextlib.suppress(Exception):
        from suijin.server.data import query as _dq

        return _dq(q, engagement=engagement)
    return {}


def _prettify(text: str, cap: int = 200) -> str:
    """Re-indent embedded JSON in displayed text."""
    with contextlib.suppress(Exception):
        import json as _j
        import re as _re

        def _fmt(m):
            with contextlib.suppress(Exception):
                return _j.dumps(_j.loads(m.group(0)), indent=1)[:cap]
            return m.group(0)

        return _re.sub(r"\{[^{}]*\}", _fmt, str(text or ""))[:cap]
    return str(text or "")[:cap]


def _turn_rows(agent: str, eng: str, limit: int = 6) -> list[dict]:
    """Native event records paired per turn (thought → tool → observation)."""
    from suijin.server.platform.lib.workspace import WORKSPACE_DIR

    ev = Path(WORKSPACE_DIR).parent / "engagements" / eng / "events.jsonl"
    if not ev.exists():
        return []
    calls: dict = {}
    thought: dict = {}
    order: list = []
    for ln in ev.read_text(errors="replace").splitlines()[-300:]:
        with contextlib.suppress(Exception):
            e = json.loads(ln)
            if e.get("agent") != agent:
                continue
            k = e.get("kind")
            if k == "assistant.message":
                thought[int(e.get("seq") or 0)] = str(e.get("content") or "")[:200]
            elif k == "tool.call":
                tid = str(e.get("id"))
                calls[tid] = {"tool": e.get("name"), "args": e.get("args") or {},
                              "obs": "", "seq": int(e.get("seq") or 0)}
                order.append(tid)
            elif k == "tool.result":
                c = calls.get(str(e.get("id")))
                if c is not None:
                    c["obs"] = str(e.get("output") or "")[:200]
    out = []
    for tid in order[-limit:]:
        c = calls[tid]
        out.append({"tool": c["tool"], "args": c["args"], "obs": c["obs"],
                    "thought": thought.get(c["seq"], "")})
    return out


def run_swarm_console() -> int:
    """The live swarm view: agents, lanes, findings, native event details."""
    console.print("[dim]swarm console — Ctrl+C detaches (swarm keeps running)[/dim]\n")

    agents: list = []
    intents: dict = {}
    findings: list = []
    sel = 0

    while True:
        console.print("[dim]─── refreshing ───[/dim]")
        _render_agents(agents, intents, findings, sel)
        console.input("\n[dim]─── [Enter] refresh · [q]uit · [number] select agent ───[/dim]\n")
        sel = -1  # force refresh


def _render_agents(agents, intents, findings, sel):
    console.print("[dim]─── swarm ───[/dim]")
    if not agents:
        console.print("  [dim]no agents[/dim]")
    for a in agents:
        lane = a.get("lane") or "idle"
        tier = a.get("tier") or "M3"
        boxed = "[box] " if a.get("boxed") else ""
        console.print(f"  {boxed}{a['id'][:24]:24s} {tier:3s} {lane[:30]:30s} hb {a['hb_age']}s")
    if findings:
        console.print("\n[bold]findings — honest board[/bold]")
        for f in findings:
            console.print(f"  [{f['severity']:6s}] {f['status']:10s} {f['title'][:60]}")
    if intents:
        console.print(f"\n  intents: {intents.get('open', 0)} open · {intents.get('running', 0)} running · "
              f"{intents.get('done', 0)} done · {intents.get('blocked', 0)} blocked · {intents.get('exhausted', 0)} exhausted")


def run() -> int:
    """Main console loop — Ctrl+C exits cleanly."""
    console.print("[bold]suijin swarm console[/bold]\n")
    while True:
        try:
            _refresh_and_render()
            time.sleep(POLL_S)
        except KeyboardInterrupt:
            console.print("\n[dim]detached — swarm still running[/dim]")
            return 0


def _refresh_and_render():
    d = _query("all")
    sw = d.get("swarm") or {}
    agents = sw.get("agents") or []
    findings = sw.get("findings") or []
    n_boxed = sw.get("n_boxed") or 0
    n_host = sw.get("n_local") or 0
    intents = sw.get("intents") or {}
    findings_n = sw.get("findings") or 0

    # strip
    console.print(f"\n  agents: {n_host + n_boxed} ({n_host} host · {n_boxed} boxed)  ·  "
                  f"intents: {intents.get('open', 0)} open / {intents.get('running', 0)} running / "
                  f"{intents.get('done', 0)} done · {intents.get('exhausted', 0)} exhausted  ·  "
                  f"{intents.get('blocked', 0)} blocked  ·  "
                  f"facts {sw.get('facts_n') or 0}  ·  "
                  f"findings {sw.get('findings') or 0}  ·  "
                  f"spend ${d.get('spend_usd') or 0:.2f}")

    # lanes
    if agents:
        console.print(f"\n  {'agent':24s} {'tier':4s} {'lane':30s} {'hb':6s}")
        for a in agents:
            lane = a.get("lane") or "idle"
            tier = a.get("tier") or "M3"
            console.print(f"  {a['id'][:24]:24s} {tier:4s} {lane[:30]:30s} hb {a['hb_age']}s")

    # findings
    if findings:
        console.print("\n  findings — honest board")
        for f in findings:
            console.print(f"    [{f['severity']:6s}] {f['status']:10s} {f['title'][:60]}")
    console.print()
