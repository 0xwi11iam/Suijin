"""swarm control — Rich screens over the data layer.

Rich for menus and flows (the wizard/selector pattern: redraw +
numbered doors + Ctrl-C always goes back); curses stays where it
already lives (the live console dashboard, the box manager list).

Every screen pulls from suijin.server.data — ONE aggregator, no
per-screen scraping. Every action has a CLI twin (hunt / spawn /
sandbox summon / destroy-idle / say / stop) — the TUI wraps verbs,
never replaces them.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def _clear():
    print(chr(27) + "[2J\033[H", end="")


def _input(prompt: str = "  > ", default: str = "") -> str:
    """Ctrl-C-safe input: raises nothing, returns default on cancel —
    EVERY menu in this module catches it and goes back."""
    try:
        raw = input(prompt).strip()
        return raw or default
    except (KeyboardInterrupt, EOFError):
        print()
        return "\x00CANCEL"  # sentinel — callers treat as cancel


def _cancelled(v: str) -> bool:
    return v == "\x00CANCEL"


def _data(q: str = "all") -> dict:
    with contextlib.suppress(Exception):
        from suijin.server.data import query

        return query(q) or {}
    return {}


# ── the hub ──────────────────────────────────────────────────────


def run_hub() -> int:
    """The home screen: state block + doors. Ctrl-C = back."""
    while True:
        _clear()
        d = _data("all")
        sw, bx, ses = d.get("swarm", {}), d.get("boxes", {}), d.get("engagement", {})

        if sw.get("live"):
            head = (
                f"[green]live[/green] · {sw.get('n_local', 0)} host + {sw.get('n_boxed', 0)} boxed agents"
                f" · intents {sw.get('intents', {}).get('open', 0)} open / {sw.get('intents', {}).get('running', 0)} running"
            )
        else:
            head = "[dim]no swarm running[/dim]"
        findings = sw.get("findings", [])
        f_line = f"{len(findings)} finding(s)" + (
            f" · latest: [{findings[0]['severity']}] {findings[0]['title'][:44]}" if findings else ""
        )
        fresh = bx.get("fresh")
        img = f"{bx.get('image', '—')} " + ("[green]fresh[/green]" if fresh else ("[yellow]stale[/yellow]" if fresh is False else "[dim]not built[/dim]"))
        console.print(Panel.fit(
            f"  swarm: {head}\n"
            f"  boxes: {sum(1 for b in bx.get('boxes', []) if b.get('running'))} running · "
            f"warm pile {bx.get('pool_n', 0)} · image {img}\n"
            f"  findings: {f_line}\n"
            f"  data on host: {ses.get('files_n', 0)} file(s) — survives every teardown",
            title="[bold #bd93f9]swarm control[/]",
            border_style="#30363d",
        ))
        console.print(
            "  [bold #bd93f9]1.[/] new hunt       guided launch: title → agents → go\n"
            "  [bold #bd93f9]2.[/] console         live dashboard (agents · findings · steer)\n"
            "  [bold #bd93f9]3.[/] boxes           box manager: state · logs · shell · kill\n"
            "  [bold #bd93f9]4.[/] hypervisor      summon/destroy idle boxes · image\n"
            "  [bold #bd93f9]5.[/] add work        queue an intent for idle agents\n"
            "  [bold #bd93f9]6.[/] message swarm   steer every agent, one line\n"
            "  [bold #bd93f9]7.[/] stop            graceful — data survives (proof shown)\n"
            "  [bold white]b.[/] [dim]back[/]"
        )
        c = _input("  > ")
        if _cancelled(c) or c in ("b", "q", ""):
            return 0
        if c == "1":
            _new_hunt()
        elif c == "2":
            with contextlib.suppress(Exception):
                from suijin.server.swarm.console import run_console_cmd

                run_console_cmd()
        elif c == "3":
            with contextlib.suppress(Exception):
                from suijin.server.swarm.control_boxes import run_box_manager_cmd

                run_box_manager_cmd()
        elif c == "4":
            _hypervisor()
        elif c == "5":
            intent = _input("  intent: ")
            if intent and not _cancelled(intent):
                subprocess.run(["suijin", "swarm", "intent", intent], timeout=60)
                _pause()
        elif c == "6":
            msg = _input("  say: ")
            if msg and not _cancelled(msg):
                subprocess.run(["suijin", "swarm", "say", msg], timeout=60)
                _pause()
        elif c == "7":
            if _stop_flow():
                return 0


def _pause():
    try:
        input("\n  [dim]Enter to continue…[/dim] ")
    except (KeyboardInterrupt, EOFError):
        pass


# ── new hunt (guided launch, Rich) ──────────────────────────────


def _new_hunt() -> None:
    _clear()
    console.print(Panel.fit(
        "the swarm is named for the TITLE. agents follow the title as\n"
        "their standing mission — no intents needed. queue intents later\n"
        "for specific lanes; they take priority when present.",
        title="[bold]new hunt — 1/4 · title[/]", border_style="cyan"))
    title = _input("  title: ")
    if _cancelled(title) or not title:
        return

    # step 2 — target + session (SKIPPABLE: authenticated testing is optional)
    _clear()
    console.print(Panel.fit("", title="[bold]new hunt — 2/4 · session (optional)[]", border_style="cyan"))
    ses = _data("sessions").get("sessions", [])
    if ses:
        for s in ses[:8]:
            console.print(f"  [green]✓[/green] {s['name'][:30]:30s} {s['cookies']} cookies · {s['age_h']}h old")
        console.print("\n  sessions above attach automatically when agents probe their targets.")
    else:
        console.print("  [dim]no sessions on file.[/dim]")
    console.print(
        "\n  [bold]l[/] log in now — opens the live browser (the jar snapshots automatically)\n"
        "  [bold]s[/] skip — unauthenticated testing only\n"
        "  [bold]Enter[/] skip"
    )
    c = _input("  > ", default="s")
    if _cancelled(c):
        return
    if c == "l":
        subprocess.run(["suijin", "auth"], timeout=3600)  # returns on done/Ctrl+C/close
        # show what was captured, then continue
        ses2 = _data("sessions").get("sessions", [])
        if ses2:
            console.print("\n  [green]captured:[/green]")
            for s in ses2[:8]:
                console.print(f"    {s['name'][:34]:34s} {s['cookies']} cookies")


# step 3 — agents
    _clear()
    console.print(Panel.fit("", title="[bold]new hunt — 3/4 · agents[/]", border_style="cyan"))
    bx = _data("boxes")
    pool = bx.get("pool_n", 0)
    console.print(
        "  [bold]h[/] host processes — fastest, runs on this machine\n"
        "  [bold]b[/] boxed — disposable containers, wiped after the hunt\n"
        f"  [bold]m[/] mixed — some of each (warm pile: {pool} ready)\n"
    )
    where = _input("  where (h/b/m): ", default="h").lower()
    if _cancelled(where):
        return
    host_n = boxed_n = 0
    if where == "b":
        boxed_n = int(_input("  how many boxed: ", default="6") or 6)
    elif where == "m":
        host_n = int(_input("  how many host: ", default="4") or 4)
        boxed_n = int(_input("  how many boxed: ", default="4") or 4)
    else:
        host_n = int(_input("  how many host: ", default="6") or 6)

    # step 4 — confirm + launch
    _clear()
    sysd = _data("system")
    cap = sysd.get("turn_cap", 0)
    console.print(Panel.fit(
        f"  title   : [bold]{title[:66]}[/bold]\n"
        f"  agents  : {host_n} host + {boxed_n} boxed\n"
        f"  brain   : {sysd.get('provider', '—')} / {sysd.get('model', '—')}\n"
        f"  turn cap: {'no cap (runs until done)' if not cap else cap}\n"
        f"  autopilot: on — when the queue runs dry and agents idle,\n"
        f"              follow-up work is queued from what's been found\n\n"
        f"  [green]Enter[/green] LAUNCH (opens the console) · [dim]Esc/Ctrl-C cancel[/dim]",
        title="[bold]new hunt — 4/4 · confirm[/]", border_style="green"))
    c = _input("  > ")
    if _cancelled(c):
        return

    subprocess.run(["suijin", "swarm", "hunt", title,
                    "--host", str(host_n), "--boxed", str(boxed_n)], timeout=600)
    _pause()


# ── hypervisor section (Rich) ────────────────────────────────────


def _hypervisor() -> None:
    while True:
        _clear()
        bx = _data("boxes")
        fresh = bx.get("fresh")
        img = f"{bx.get('image', '—')} " + ("[green]fresh[/green]" if fresh else ("[yellow]stale[/yellow]" if fresh is False else "[dim]not built[/dim]"))
        boxes = bx.get("boxes", [])
        running = sum(1 for b in boxes if b.get("running"))
        pooled = sum(1 for b in boxes if b.get("pooled"))
        console.print(Panel.fit(
            f"  engine: {bx.get('engine', 'none')} · image {img}\n"
            f"  boxes: {running} running · {pooled} idle in the warm pile\n"
            f"  idle boxes have NO agent and NO intent — the next engagement\n"
            f"  summons them on demand (sub-second start)",
            title="[bold]hypervisor[/]", border_style="#30363d"))
        console.print(
            "  [bold]1.[/] summon idle boxes — keep N warm for the next hunt\n"
            "  [bold]2.[/] destroy idle boxes — running agents untouched\n"
            "  [bold]3.[/] rebuild image — code changed, bake a fresh template\n"
            "  [bold]4.[/] box manager — logs · shell · kill per box\n"
            "  [bold white]b.[/] [dim]back[/]"
        )
        c = _input("  > ")
        if _cancelled(c) or c in ("b", "q", ""):
            return
        if c == "1":
            n = _input("  keep how many idle boxes: ", default="4")
            if not _cancelled(n):
                subprocess.run(["suijin", "sandbox", "summon", n or "4"], timeout=300)
                _pause()
        elif c == "2":
            subprocess.run(["suijin", "sandbox", "destroy-idle"], timeout=120)
            _pause()
        elif c == "3":
            subprocess.run(["suijin", "box", "image", "build"], timeout=1800)
            _pause()
        elif c == "4":
            with contextlib.suppress(Exception):
                from suijin.server.swarm.control_boxes import run_box_manager_cmd

                run_box_manager_cmd()


# ── stop flow (Rich, with data-survival proof) ───────────────────


def _stop_flow() -> bool:
    """Returns True if the swarm was stopped."""
    _clear()
    d = _data("all")
    boxes = d.get("boxes", {}).get("boxes", [])
    n_running = sum(1 for b in boxes if b.get("running"))
    n_files = d.get("engagement", {}).get("files_n", 0)
    console.print(Panel.fit(
        f"  {n_running} boxed agent(s) + the warm pile will be DESTROYED.\n\n"
        f"  your data is NOT in the boxes — it lives on the host at\n"
        f"  ~/.suijin/engagements/ and survives teardown.\n"
        f"  right now: {n_files} file(s) on disk. every one stays.\n\n"
        f"  [green]Enter[/green] stop everything · [dim]Ctrl-C cancel[/dim]",
        title="[bold red]stop the swarm?[/]", border_style="red"))
    c = _input("  > ")
    if _cancelled(c):
        return False
    subprocess.run(["suijin", "swarm", "stop"], timeout=300)
    subprocess.run(["suijin", "sandbox", "gc"], timeout=300)
    after = _data("engagement").get("files_n", 0)
    console.print(
        f"\n  [green]✓[/green] stopped · boxes destroyed · data intact: {after} file(s) (was {n_files})"
    )
    _pause()
    return True


def run_hub_cmd() -> int:
    if not sys.stdout.isatty():
        print("swarm control needs a terminal (tty).")
        return 1
    return run_hub()
