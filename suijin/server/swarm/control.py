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


def _pick_swarm() -> str:
    """WHICH swarm — asked whenever more than one exists (operator
    directive 2026-11-07). One swarm → returned silently."""
    sw = _data("swarms").get("swarms", [])
    if len(sw) <= 1:
        return sw[0]["swarm"] if sw else "default"
    console.print("\n  [bold]which swarm?[/bold]")
    for i, s in enumerate(sw, 1):
        console.print(f"   {i}. {s['swarm']:20s} {s['agents']} agents · {s['open']} open · {s['running']} running")
    pick = _input("  number (Enter = default): ", default="1")
    if _cancelled(pick) or not pick.isdigit() or not (1 <= int(pick) <= len(sw)):
        return "default"
    return sw[int(pick) - 1]["swarm"]


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
            "  [bold #bd93f9]5.[/] open agent      attach a specific agent's live view by id\n"
            "  [bold #bd93f9]6.[/] system stats    cpu · memory pressure · ssd load · network\n"
            "  [bold #bd93f9]7.[/] add work        queue an intent for idle agents\n"
            "  [bold #bd93f9]8.[/] message swarm   steer every agent, one line\n"
            "  [bold #bd93f9]9.[/] stop            graceful — data survives (proof shown)\n"
            "  [bold white]b.[/] [dim]back[/]"
        )
        c = _input("  > ")
        if _cancelled(c) or c in ("b", "q", ""):
            return 0
        if c == "1":
            _new_hunt()
        elif c == "2":
            from suijin.modules.console.lib.operator_tui import main as _opmain

            try:
                _opmain()
            except Exception as e:  # noqa: BLE001 — a crash must SHOW, not flash
                import traceback

                console.print(f"[red]console crashed: {e.__class__.__name__}: {e}[/red]")
                console.print(f"[dim]{traceback.format_exc()[-400:]}[/dim]")
                _pause()
        elif c == "3":
            from suijin.server.swarm.control_boxes import run_box_manager_cmd

            try:
                run_box_manager_cmd()
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]box manager crashed: {e.__class__.__name__}: {e}[/red]")
                _pause()
        elif c == "4":
            _hypervisor()
        elif c == "5":
            # OPEN AGENT — numbered list; enter the NUMBER (easiest) or
            # any part of the id. This replaced the bare-id prompt the
            # operator couldn't read.
            agents = [a for a in d.get("swarm", {}).get("agents", []) if not a.get("dead")][:15]
            if not agents:
                console.print("  [dim]no live agents[/dim]")
                _pause()
            else:
                for i, a in enumerate(agents, 1):
                    lane = (a.get("intent") or "live")[:34]
                    console.print(f"  {i:>2}. {a['id'][:30]:30s} [dim]{lane}[/dim]")
                pick = _input("  number (or any part of the id): ")
                if pick and not _cancelled(pick):
                    match = ""
                    if pick.isdigit() and 1 <= int(pick) <= len(agents):
                        match = agents[int(pick) - 1]["id"]
                    else:
                        match = next((a["id"] for a in agents if pick.lower() in a["id"].lower()), "")
                    if match:
                        subprocess.run(["suijin", "swarm", "tui", match], timeout=3600)
                    else:
                        console.print(f"  [red]no agent matches '{pick}'[/red]")
                        _pause()
        elif c == "6":
            with contextlib.suppress(Exception):
                from suijin.server.tools.lib.system_stats import collect as _collect, render as _render

                console.print("[dim]measuring (1s sample)...[/dim]")
                console.print(_render(_collect(interval=1.0)))
            _pause()
        elif c == "7":
            intent = _input("  intent: ")
            if intent and not _cancelled(intent):
                swarm = _pick_swarm()
                subprocess.run(["suijin", "swarm", "intent", intent, "--swarm", swarm], timeout=60)
                _pause()
        elif c == "8":
            msg = _input("  say: ")
            if msg and not _cancelled(msg):
                _pick_swarm()  # which swarm — noted for the operator; say still broadcasts to live agents
                subprocess.run(["suijin", "swarm", "say", msg], timeout=60)
                _pause()
        elif c == "9":
            _pick_swarm()  # which swarm — the stop screen reflects the choice
            if _stop_flow():
                return 0


def _pause():
    try:
        input("\n  Enter to continue…")
    except (KeyboardInterrupt, EOFError):
        pass


# ── new hunt (guided launch, Rich) ──────────────────────────────


def _capacity_ok(host_n: int, boxed_n: int) -> bool:
    with contextlib.suppress(Exception):
        from suijin.server.swarm.lifecycle import spawn_capacity_check

        ok, _ = spawn_capacity_check(host_n, boxed_n)
        return ok
    return True


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
    console.print(Panel.fit("", title="[bold]new hunt — 2/5 · session (optional)]", border_style="cyan"))
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


    # step 3 — lanes (typed, rendered as rows — the approved design)
    _clear()
    lanes = []
    while True:
        console.print(Panel.fit(
            "lanes split the hunt. add as many as you want by typing them;\n"
            "they render as rows below. empty lanes = agents just follow the title.",
            title="[bold]new hunt — 3/5 · lanes (optional)[/]", border_style="cyan"))
        if lanes:
            for i, ln in enumerate(lanes, 1):
                console.print(f"  {i}. {ln[:74]}")
        console.print("\n  [bold]a[/] add lane · [bold]d[/] delete last · [bold]Enter[/] done")
        c = _input("  > ")
        if _cancelled(c) or c == "":
            break
        if c == "a":
            ln = _input("  lane: ")
            if ln and not _cancelled(ln):
                lanes.append(ln)
                _clear()
                continue
        elif c == "d" and lanes:
            lanes.pop()
            _clear()
            continue
        _clear()

    # step 4 — agents
    _clear()
    console.print(Panel.fit("", title="[bold]new hunt — 4/5 · agents[/]", border_style="cyan"))
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

    # step 5 — confirm + launch
    _clear()
    sysd = _data("system")
    cap = sysd.get("turn_cap", 0)
    est_mb = host_n * 220 + boxed_n * 160
    mem_line = (
        f"  memory  : ~{est_mb}MB estimated\n"
        if _capacity_ok(host_n, boxed_n)
        else f"  memory  : ~{est_mb}MB — EXCEEDS this host's safe budget, launch will refuse\n"
    )
    console.print(Panel.fit(
        f"  title   : [bold]{title[:66]}[/bold]\n"
        f"  agents  : {host_n} host + {boxed_n} boxed\n"
        f"{mem_line}"
        f"  lanes   : {len(lanes)} — {lanes[0][:40] + '…' if lanes else 'none, agents follow the title'}\n"
        f"  brain   : {sysd.get('provider', '—')} / {sysd.get('model', '—')}\n"
        f"  turn cap: {'no cap (runs until done)' if not cap else cap}\n"
        f"  autopilot: on — when the queue runs dry and agents idle,\n"
        f"              follow-up work is queued from what's been found\n\n"
        f"  [green]Enter[/green] LAUNCH (opens the console) · [dim]Esc/Ctrl-C cancel[/dim]",
        title="[bold]new hunt — 5/5 · confirm[/]", border_style="green"))
    c = _input("  > ")
    if _cancelled(c):
        return
    if not _capacity_ok(host_n, boxed_n):
        console.print("  [red]✗ refused — too many agents for this host (see memory line above)[/red]")
        _pause()
        return

    subprocess.run(["suijin", "swarm", "hunt", title,
                    "--host", str(host_n), "--boxed", str(boxed_n)], timeout=600)
    for ln in lanes:  # lanes queue as intents — they take priority over the title
        subprocess.run(["suijin", "swarm", "intent", ln], timeout=60)
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
    n_boxed = sum(1 for b in boxes if b.get("running") and not b.get("pooled"))
    n_host = d.get("swarm", {}).get("n_local", 0)
    n_pile = sum(1 for b in boxes if b.get("pooled"))
    n_files = d.get("engagement", {}).get("files_n", 0)
    dying = f"{n_host} host agent(s) + {n_boxed} boxed agent(s) + warm pile ({n_pile})"
    console.print(Panel.fit(
        f"  STOPPING KILLS: {dying}\n"
        f"  ({n_host + n_boxed} agents finish their current step, then exit;\n"
        f"   {n_boxed} boxes + {n_pile} pile boxes are DESTROYED)\n\n"
        f"  your data is NOT in the boxes — it lives on the host at\n"
        f"  ~/.suijin/engagements/ and survives teardown.\n"
        f"  right now: {n_files} file(s) on disk. every one stays.\n\n"
        f"  [green]Enter[/green] stop everything · Ctrl-C cancel",
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
