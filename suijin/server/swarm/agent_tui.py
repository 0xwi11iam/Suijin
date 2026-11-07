"""suijin swarm tui <agent> — the engagement TUI as a bus subscriber.

The red-team TUI experience, driven from the BUS: subscribe to
/engagement/<eng>/events, render one agent's live stream — thought →
❯ tool + prettified args → observation, findings highlighted, notes.
Works for ANY agent: red team's own run (event_log publishes to the
bus now), host swarm agents, boxed agents (same bus via
host.docker.internal). History primes from events.jsonl, then the bus
takes over live.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
import time
from pathlib import Path

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

REFRESH = 0.4


def _ws_root() -> Path:
    with contextlib.suppress(Exception):
        from suijin.server.platform.lib.workspace import WORKSPACE_DIR

        return Path(WORKSPACE_DIR).parent
    return Path.home() / ".suijin"


def _resolve(frag: str, eng: str) -> str | None:
    """Fragment → full agent id (from events.jsonl agent tags)."""
    ev = _ws_root() / "engagements" / eng / "events.jsonl"
    if not ev.exists():
        return None
    ids = set()
    for ln in ev.read_text(errors="replace").splitlines():
        with contextlib.suppress(Exception):
            e = json.loads(ln)
            a = e.get("agent")
            if a:
                ids.add(a)
    frag = frag.strip().lower()
    for a in ids:
        if frag in a.lower():
            return a
    return None


def _history(agent: str, eng: str, limit: int = 30) -> list[dict]:
    """Prime the stream from events.jsonl (boxed agents write through
    the mount; host agents write the same file)."""
    ev = _ws_root() / "engagements" / eng / "events.jsonl"
    calls: dict = {}
    thought: dict = {}
    order: list = []
    if not ev.exists():
        return []
    for ln in ev.read_text(errors="replace").splitlines():
        with contextlib.suppress(Exception):
            e = json.loads(ln)
            if e.get("agent") != agent:
                continue
            k = e.get("kind")
            if k == "assistant.message":
                thought[int(e.get("seq") or 0)] = str(e.get("content") or "")[:400]
            elif k == "tool.call":
                calls[str(e.get("id"))] = {"tool": e.get("name"), "args": e.get("args") or {},
                                           "seq": int(e.get("seq") or 0), "obs": ""}
                order.append(str(e.get("id")))
            elif k == "tool.result":
                c = calls.get(str(e.get("id")))
                if c is not None:
                    c["obs"] = str(e.get("output") or "")[:600]
    out = []
    for tid in order[-limit:]:
        c = calls[tid]
        out.append({"tool": c["tool"], "args": c["args"], "obs": c["obs"],
                    "thought": thought.get(c["seq"], "")})
    return out


def _bus_stream(agent: str, eng: str, sink: list, stop) -> None:
    """Background thread: subscribe to the bus, feed the sink live."""
    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import BusClient, discover_bus

        addr = discover_bus()
        if not addr:
            return
        bc = BusClient()
        if not bc.connect(addr[0], addr[1], f"tui-{agent[:12]}"):
            return
        bc.subscribe(f"/engagement/{eng}/events")
        calls: dict = {}
        while not stop.is_set():
            for m in bc.drain(50):
                with contextlib.suppress(Exception):
                    e = m.get("payload") or {}
                    if e.get("agent") != agent:
                        continue
                    k = e.get("kind")
                    if k == "tool.call":
                        calls[str(e.get("id"))] = {"tool": e.get("name"), "args": e.get("args") or {},
                                                   "obs": "", "thought": ""}
                        sink.append(calls[str(e.get("id"))])
                    elif k == "tool.result":
                        c = calls.get(str(e.get("id")))
                        if c is not None:
                            c["obs"] = str(e.get("output") or "")[:600]
                    elif k == "assistant.message":
                        if sink:
                            sink[-1]["thought"] = str(e.get("content") or "")[:400]
            time.sleep(0.2)


def _render_turn(t: dict) -> Group:
    """One turn, the engagement TUI's exact shape."""
    parts = []
    if t.get("thought"):
        th = Text("  | thought: ", style="dim cyan")
        th.append(" ".join(str(t["thought"]).split())[:110], style="white")
        parts.append(th)
    if t.get("tool"):
        parts.append(Text(f"  ❯ {t['tool']}", style="bold green"))
        args = t.get("args") or {}
        if args:
            shown = {k: (v if len(str(v)) <= 64 else str(v)[:61] + "…") for k, v in args.items()}
            try:
                body = json.dumps(shown, ensure_ascii=False, indent=2)
            except Exception:
                body = str(shown)
            parts.append(Syntax("  " + body.replace("\n", "\n  "), "json",
                                theme="monokai", word_wrap=True, background_color="default"))
    obs = str(t.get("obs") or "")
    if obs:
        hot = "CONFIRMED" in obs
        parts.append(Rule(style="dim"))
        parts.append(Text("  " + obs[:400], style="bold yellow" if hot else "grey74", overflow="fold"))
    return Group(*parts)


def run_agent_tui(frag: str, engagement: str = "_default") -> int:
    from rich.console import Console

    console = Console()
    agent = _resolve(frag, engagement)
    if not agent:
        console.print(f"[red]no agent matches '{frag}' in {engagement}[/red]")
        return 1

    turns: list = list(_history(agent, engagement))
    import threading

    stop = threading.Event()
    t = threading.Thread(target=_bus_stream, args=(agent, engagement, turns, stop), daemon=True)
    t.start()

    console.print(f"[dim]attaching {agent} — history {len(turns)} turn(s), bus live. q to detach.[/dim]")
    time.sleep(0.5)
    try:
        with Live(console=console, refresh_per_second=4, screen=True) as live:
            while True:
                shown = turns[-14:]
                body = Group(*[Group(_render_turn(x), Text("")) for x in shown])
                live.update(Panel(
                    body,
                    title=f"[bold]suijin · {agent[:30]}[/bold] · {len(turns)} turns · live from the bus",
                    border_style="#bd93f9",
                ))
                time.sleep(REFRESH)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
    console.print("[dim]detached[/dim]")
    return 0
