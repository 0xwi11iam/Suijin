"""the engagement TUI as a BUS CLIENT — the real UI, fed from the wire.

`suijin swarm tui <agent>` — the ACTUAL EngagementUI (the same renderer
the red-team run uses: iteration headers, thought, ❯ tool with lexed
args, output blocks, phase transitions), driven by events instead of an
in-process graph:

  history: events.jsonl replayed through the UI (boxed agents write
           through the mount — same file)
  live:    /engagement/<eng>/events from the bus — every event_log
           record now publishes there (red-team graph AND swarm workers
           speak the same stream)

The translation layer maps native records to UI calls:
  iteration        → ui.iteration_header(n, phase)
  assistant.message→ ui.thinking(content)
  tool.call        → ui.tool(name, args)
  tool.result      → ui.output(output)
  phase.transition → ui.phase_transition(to)

This is the client-server seam the operator asked for: any agent, any
node type, the one true TUI.
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path


def _ws_root() -> Path:
    with contextlib.suppress(Exception):
        from suijin.server.platform.lib.workspace import WORKSPACE_DIR

        return Path(WORKSPACE_DIR).parent
    return Path.home() / ".suijin"


def _events_path(eng: str) -> Path:
    return _ws_root() / "engagements" / eng / "events.jsonl"


def resolve_agent(frag: str, eng: str) -> str | None:
    ev = _events_path(eng)
    if not ev.exists():
        return None
    ids: set = set()
    for ln in ev.read_text(errors="replace").splitlines():
        with contextlib.suppress(Exception):
            e = json.loads(ln)
            if e.get("agent"):
                ids.add(e["agent"])
    frag = frag.strip().lower()
    for a in ids:
        if frag in a.lower():
            return a
    return None


def _apply(ui, e: dict) -> None:
    """One native record → one EngagementUI call (the graph's own order)."""
    k = e.get("kind")
    if k == "iteration":
        ui.iteration_header(int(e.get("n") or 1), str(e.get("phase") or "working"))
    elif k == "assistant.message":
        ui.thinking(str(e.get("content") or "")[:400])
    elif k == "tool.call":
        ui.tool(str(e.get("name") or "?"), e.get("args") or {})
    elif k == "tool.result":
        ui.output(str(e.get("output") or ""))
    elif k == "phase.transition":
        ui.phase_transition(str(e.get("to") or ""))


def run(frag: str, engagement: str = "_default") -> int:
    from rich.console import Console

    console = Console()
    agent = resolve_agent(frag, engagement)
    if not agent:
        console.print(f"[red]no agent matches '{frag}' in {engagement}[/red]")
        return 1

    from suijin.client.tui.console_ui import EngagementUI

    ui = EngagementUI(console, objective=agent)
    console.print(f"[dim]attaching [bold]{agent}[/bold] — replaying history, then live from the bus (Ctrl-C detaches)[/dim]\n")

    # 1. HISTORY — replay through the real UI
    n = 0
    seen: set = set()
    if _events_path(engagement).exists():
        for ln in _events_path(engagement).read_text(errors="replace").splitlines():
            with contextlib.suppress(Exception):
                e = json.loads(ln)
                if e.get("agent") != agent:
                    continue
                key = (e.get("kind"), e.get("seq"), e.get("id"))
                if key in seen:
                    continue
                seen.add(key)
                _apply(ui, e)
                n += 1
    console.print(f"\n[dim]— history: {n} record(s) replayed — live below —[/dim]\n")

    # 2. LIVE — subscribe to the bus; translate as records arrive
    import threading

    stop = threading.Event()
    got = {"n": 0}

    def _live():
        with contextlib.suppress(Exception):
            from suijin.server.mesh.bus import BusClient, discover_bus

            addr = discover_bus()
            if not addr:
                console.print("[yellow]no bus — history only (the agent runs without a coordinator)[/yellow]")
                return
            bc = BusClient()
            if not bc.connect(addr[0], addr[1], f"tui-{agent[:12]}"):
                console.print("[yellow]bus unreachable — history only[/yellow]")
                return
            bc.subscribe(f"/engagement/{engagement}/events")
            while not stop.is_set():
                for m in bc.drain(50):
                    with contextlib.suppress(Exception):
                        e = m.get("payload") or {}
                        if e.get("agent") != agent:
                            continue
                        key = (e.get("kind"), e.get("seq"), e.get("id"))
                        if key in seen:
                            continue
                        seen.add(key)
                        _apply(ui, e)
                        got["n"] += 1
                time.sleep(0.2)

    t = threading.Thread(target=_live, daemon=True)
    t.start()
    try:
        while t.is_alive():
            t.join(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            ui._flush()
    console.print(f"\n[dim]detached — {got['n']} live record(s) this session[/dim]")
    return 0
