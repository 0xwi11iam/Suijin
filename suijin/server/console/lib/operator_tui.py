"""Suijin Operator — the multi-agent command center.

One screen, every agent, zero terminal-flipping: live roster with phase
badges and pulse indicators, per-agent detail (thought, last tool,
findings with severity colors), aggregate cost/iterations/findings, the
mesh coordination feed, and a command bar.

Data sources (all files the running system already writes):
  ~/.suijin/mesh/<pid>.json         — registry: pid, target, phase, beat, port
  ~/.suijin/mesh/<pid>-state.json   — digest: iteration, findings, footholds
  engagements/*/audit_trails/*.json  — full iterations, cost, findings
  ~/.suijin/mesh/gc.log             — coordination feed

`suijin operator` — live. `suijin operator --demo` — mock data.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ── palette ─────────────────────────────────────────────────────────────
GOLD = "#e8b64c"
CYAN = "#5ec7d6"
RED = "#e05555"
YELLOW = "#e8c45c"
GREEN = "#6bd48a"
DIM = "#707880"
WHITE = "#d8dee4"
BG = "#161a1f"

SEV_STYLE = {
    "critical": "bold red on #2a1215",
    "high": "bold red",
    "medium": "bold yellow",
    "low": "yellow",
    "info": "cyan",
    "note": "dim",
}

PHASE_STYLE = {
    "recon": "cyan",
    "informational": "bright_cyan",
    "exploitation": "yellow",
    "post_exploitation": "green",
}

PULSE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

MESH_DIR = Path.home() / ".suijin" / "mesh"
WORKSPACE_DIR = Path.home() / ".suijin" / "workspace"
POLL_S = 2.0


# ═══════════════════════════════════════════════════════════════════════
# Data layer
# ═══════════════════════════════════════════════════════════════════════


class Agent:
    __slots__ = (
        "pid",
        "target",
        "phase",
        "iteration",
        "findings",
        "footholds",
        "recent_actions",
        "beat",
        "port",
        "started",
        "cost_usd",
        "total_actions",
        "ok_actions",
        "fail_actions",
        "last_thought",
        "last_tool",
        "last_obs",
        "engagement_dir",
        "audit_data",
    )

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))
        self.audit_data = None

    @property
    def alive(self) -> bool:
        return time.time() - self.beat < 30

    @property
    def uptime(self) -> float:
        return time.time() - self.started

    @property
    def conf_finds(self) -> int:
        return sum(1 for f in (self.findings or []) if str(f.get("severity", "")).lower() in ("high", "critical"))

    @property
    def all_finds(self) -> int:
        return len(self.findings or [])


def _rj(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _iso_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def scan_agents(mesh_dir: Path, workspace: Path) -> list[Agent]:
    """Live mesh nodes, enriched with audit-trail detail."""
    now = time.time()
    agents: list[Agent] = []
    if not mesh_dir.is_dir():
        return agents

    # index audit trails by started timestamp for matching
    audits: list[tuple[float, dict, Path]] = []
    eng_root = workspace / "engagements"
    if eng_root.is_dir():
        for d in eng_root.iterdir():
            at = d / "audit_trails"
            if not at.is_dir():
                continue
            for f in at.glob("*.json"):
                if f.name.startswith("agent_steps"):
                    continue
                data = _rj(f)
                if data and data.get("started"):
                    audits.append((_iso_ts(data["started"]), data, d))
    audits.sort(key=lambda x: -x[0])

    for f in sorted(mesh_dir.glob("*.json")):
        if f.name.endswith("-state.json") or f.name == "remote-peers.json":
            continue
        rec = _rj(f)
        pid = int(rec.get("pid") or 0)
        if not pid or pid == os.getpid():
            continue
        beat = float(rec.get("beat") or 0)
        if now - beat > 30:
            continue

        state = _rj(mesh_dir / f"{pid}-state.json")
        a = Agent(
            pid=pid,
            target=str(rec.get("summary", "?"))[:50],
            phase=str(state.get("phase") or rec.get("phase") or "?"),
            iteration=int(state.get("iteration") or 0),
            findings=state.get("findings") or [],
            footholds=state.get("footholds") or [],
            recent_actions=[str(x)[:70] for x in (state.get("recent_actions") or [])],
            beat=beat,
            port=int(rec.get("port") or 0),
            started=float(rec.get("started") or 0),
        )

        # match audit by closest timestamp (within 60s)
        best_gap = 999
        for ts, data, eng_dir in audits:
            gap = abs(ts - a.started)
            if gap < min(best_gap, 60):
                best_gap = gap
                a.audit_data = data
                a.engagement_dir = eng_dir

        if a.audit_data:
            d = a.audit_data
            a.cost_usd = float(d.get("cost_usd") or 0)
            a.total_actions = int(d.get("total_actions") or 0)
            a.ok_actions = int(d.get("successful_actions") or 0)
            a.fail_actions = int(d.get("failed_actions") or 0)
            iters = d.get("iterations") or []
            if iters:
                last = iters[-1]
                a.last_thought = str(last.get("thought") or "")[:140]
                act = last.get("action") or {}
                a.last_tool = str(act.get("tool") or "?")
                a.last_obs = str(last.get("observation") or "")[:140]
            if not a.findings:
                a.findings = d.get("findings") or []

        agents.append(a)

    agents.sort(key=lambda x: -x.iteration)
    return agents


def read_chat(mesh_dir: Path, limit: int = 12) -> list[str]:
    p = mesh_dir / "gc.log"
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(errors="replace").splitlines()[-limit:]:
        parts = ln.split("|", 3)
        if len(parts) == 4:
            ts, pid, name, msg = parts
            out.append(f"{ts} [{name}:{pid}] {msg[:90]}")
    return out


# ═══════════════════════════════════════════════════════════════════════
# Mock data
# ═══════════════════════════════════════════════════════════════════════


def write_demo(mesh: Path, ws: Path) -> None:
    mesh.mkdir(parents=True, exist_ok=True)
    now = time.time()

    specs = [
        {
            "pid": 91001,
            "phase": "post_exploitation",
            "iter": 34,
            "cost": 0.019,
            "thought": "Credential grid exhausted — pivoting to /files/ surface for upload paths",
            "tool": "http_request",
            "obs": "Status: 404 — %2F-encoded traversal rejected",
            "ok": 32,
            "fail": 2,
            "started_offset": 920,
            "finds": [
                {
                    "severity": "high",
                    "type": "mass_assignment",
                    "endpoint": "/api/v1/me",
                    "description": "PUT accepts 'role' from body — privilege escalation",
                },
                {
                    "severity": "medium",
                    "type": "idor",
                    "endpoint": "/api/v1/invoices/EXT-*",
                    "description": "External invoices skip tenant check",
                },
            ],
        },
        {
            "pid": 91002,
            "phase": "exploitation",
            "iter": 20,
            "cost": 0.014,
            "thought": "Auth layer validates tokens — testing alg confusion with public cert",
            "tool": "execute_terminal",
            "obs": "python3: HS256 with cert PEM accepted",
            "ok": 18,
            "fail": 2,
            "started_offset": 880,
            "finds": [
                {
                    "severity": "critical",
                    "type": "jwt_confusion",
                    "endpoint": "/api/v1/me",
                    "description": "HS256 keyed with public cert — forgeable tokens",
                },
            ],
        },
        {
            "pid": 91003,
            "phase": "recon",
            "iter": 12,
            "cost": 0.009,
            "thought": "Fingerprinting edge gateway — BaseHTTP proxy → FastAPI core-api",
            "tool": "http_request",
            "obs": "Status: 200 — root serves marketing HTML",
            "ok": 12,
            "fail": 0,
            "started_offset": 240,
            "finds": [],
        },
    ]

    for s in specs:
        started = now - s["started_offset"]
        iso = datetime.fromtimestamp(started, tz=timezone.utc).isoformat()

        (mesh / f"{s['pid']}.json").write_text(
            json.dumps(
                {
                    "pid": s["pid"],
                    "summary": "127.0.0.1:6000",
                    "phase": s["phase"],
                    "started": started,
                    "beat": now - 2,
                    "port": 50000 + s["pid"] % 1000,
                }
            )
        )
        (mesh / f"{s['pid']}-state.json").write_text(
            json.dumps(
                {
                    "pid": s["pid"],
                    "phase": s["phase"],
                    "iteration": s["iter"],
                    "findings": s["finds"],
                    "footholds": [],
                    "recent_actions": [
                        f"{s['tool']}: {s['thought'][:55]}",
                        "http_request: probing /api/v1/ping → 200",
                        "surface_expand: mapped /api/v1/* noun routes",
                    ],
                }
            )
        )
        eng = ws / "engagements" / f"demo_{s['pid']}_127_0_0_1_6000"
        at = eng / "audit_trails"
        at.mkdir(parents=True, exist_ok=True)
        (at / "127_0_0_1_6000.json").write_text(
            json.dumps(
                {
                    "engagement": "127.0.0.1:6000",
                    "started": iso,
                    "ended": None,
                    "iterations": [
                        {
                            "iteration": s["iter"],
                            "timestamp": iso,
                            "phase": s["phase"],
                            "thought": s["thought"],
                            "reasoning": "",
                            "action": {"tool": s["tool"], "args": {}, "success": True},
                            "observation": s["obs"],
                            "chain_context": "",
                            "completion_reason": "",
                        }
                    ],
                    "findings": [{"timestamp": iso, **f} for f in s["finds"]],
                    "total_actions": s["iter"],
                    "successful_actions": s["ok"],
                    "failed_actions": s["fail"],
                    "cost_usd": s["cost"],
                },
                indent=1,
            )
        )

    chat_lines = []
    for s in specs:
        ts = datetime.fromtimestamp(started + 60, tz=timezone.utc).strftime("%H:%M:%S")
        chat_lines.append(f"{ts}|{s['pid']}|agent|{s['thought'][:70]}")
    (mesh / "gc.log").write_text("\n".join(chat_lines) + "\n")


# ═══════════════════════════════════════════════════════════════════════
# Rendering
# ═══════════════════════════════════════════════════════════════════════

_frame = {"i": 0}


def _pulse() -> str:
    _frame["i"] = (_frame["i"] + 1) % len(PULSE_FRAMES)
    return PULSE_FRAMES[_frame["i"]]


def _cost_color(cost: float) -> str:
    if cost >= 0.50:
        return "bold red"
    if cost >= 0.20:
        return "yellow"
    return "green"


def _fmt_dur(seconds: float) -> str:
    m = int(seconds // 60)
    if m >= 60:
        return f"{m // 60}h{m % 60:02d}m"
    s = int(seconds % 60)
    return f"{m:02d}m{s:02d}s"


def _phase_badge(phase: str) -> Text:
    style = PHASE_STYLE.get(phase, "white")
    icon = {"recon": "◎", "informational": "◎", "exploitation": "⚡", "post_exploitation": "◆"}.get(phase, "?")
    return Text(f" {icon} {phase} ", style=f"bold {style}")


def _sev_badge(sev: str) -> Text:
    s = str(sev or "info").lower()
    style = SEV_STYLE.get(s, "dim")
    return Text(f" ● {s.upper()} ", style=style)


def _bar(current: int, total: int, width: int = 20, style: str = "cyan") -> Text:
    if total <= 0:
        return Text(" " * width, style="dim")
    filled = min(width, int(width * current / max(1, total)))
    return Text("█" * filled + "░" * (width - filled), style=style)


class OperatorTUI:
    def __init__(self, mesh_dir: Path, workspace: Path):
        self.mesh_dir = mesh_dir
        self.workspace = workspace
        self.console = Console()
        self.agents: list[Agent] = []
        self.chat: list[str] = []
        self.selected = 0
        self.running = True
        self.note = ""
        self._lock = threading.Lock()

    def start_poll(self) -> None:
        def _poll():
            while self.running:
                try:
                    agents = scan_agents(self.mesh_dir, self.workspace)
                    chat = read_chat(self.mesh_dir)
                    with self._lock:
                        self.agents = agents
                        self.chat = chat
                        if self.selected >= len(agents):
                            self.selected = max(0, len(agents) - 1)
                except Exception:
                    pass
                time.sleep(POLL_S)

        threading.Thread(target=_poll, daemon=True, name="operator-poll").start()

    # ── panel renderers ─────────────────────────────────────────────

    def _header(self) -> Text:
        n = len(self.agents)
        finds = sum(a.all_finds for a in self.agents)
        conf = sum(a.conf_finds for a in self.agents)
        cost = sum(a.cost_usd or 0 for a in self.agents)

        left = Text.assemble(
            (_pulse(), f"bold {GOLD}"),
            (" SUIJIN OPERATOR ", f"bold {GOLD}"),
            ("│ ", DIM),
            (f"{n}", "bold white"),
            (" agent", "dim"),
            ("s" if n != 1 else "", "dim"),
            ("  │  ", DIM),
            (f"${cost:.3f}", f"bold {_cost_color(cost)}"),
            ("  │  ", DIM),
            (f"{finds}", "bold white"),
            (" findings  ", "dim"),
            (f"{conf}", f"bold {RED}" if conf else "dim"),
            (" high+", "dim"),
        )
        return left

    def _agents_panel(self) -> Panel:
        t = Table.grid(expand=True, padding=(0, 1, 0, 1))
        t.add_column(width=3)
        t.add_column(ratio=1)

        for i, a in enumerate(self.agents):
            sel = i == self.selected
            marker = Text("▸" if sel else " ", style=f"bold {GOLD}" if sel else "dim")
            pulse = _pulse() if a.alive else "✗"

            row_text = Text()
            row_text.append(f" {pulse} ", style=f"bold {GREEN}" if a.alive else "bold red")
            row_text.append(f"{a.pid}", style=f"bold {WHITE}" if sel else WHITE)
            row_text.append(f"  {a.target}", style=DIM)
            row_text.append("\n    ")
            row_text.append(_phase_badge(a.phase))
            cost_str = f"${a.cost_usd:.3f}" if a.cost_usd else "—"
            row_text.append(f"  iter {a.iteration:3d}  {cost_str}  ", style=DIM)
            row_text.append(_fmt_dur(a.uptime), style="dim italic")
            if a.all_finds:
                row_text.append(f"  {a.conf_finds}◆ {a.all_finds}●", style=f"bold {RED}" if a.conf_finds else "dim")

            t.add_row(marker, row_text)

        if not self.agents:
            t.add_row(Text(), Text("  (no live agents — start engagements)", style=DIM))

        return Panel(
            t,
            title=f" [{GOLD}]agents[/{GOLD}] ",
            border_style=f"{'bold ' if self.agents else ''}{DIM}",
            padding=(0, 0),
        )

    def _detail_panel(self) -> Panel:
        if not self.agents:
            return Panel(Text("  select an agent ↑↓", style=DIM), title=" detail ", border_style=DIM)
        a = self.agents[self.selected]

        lines = Text()
        lines.append(f"  {a.pid}", style=f"bold {WHITE}")
        lines.append(f"  {a.target}", style=DIM)
        lines.append("  ")
        lines.append(_phase_badge(a.phase))
        lines.append(f"  iter {a.iteration}", style=DIM)
        lines.append("\n")

        if a.last_thought:
            lines.append(
                Text.assemble(
                    ("  ┃ ", f"{CYAN}"),
                    ("thought: ", f"dim {CYAN}"),
                    (a.last_thought, WHITE),
                    ("\n", ""),
                )
            )

        if a.last_tool:
            lines.append(
                Text.assemble(
                    ("  ┃ ", f"{GOLD}"),
                    ("last: ", DIM),
                    (a.last_tool, f"bold {GOLD}"),
                    (f" → {a.last_obs[:80]}", DIM),
                    ("\n", ""),
                )
            )

        # activity bar
        if a.total_actions:
            lines.append(
                Text.assemble(
                    ("  ┃ ", DIM),
                    ("act  ", DIM),
                    _bar(a.ok_actions, a.total_actions, 25, GREEN),
                    (f"  {a.ok_actions}/{a.total_actions}", f"bold {GREEN}" if a.fail_actions == 0 else YELLOW),
                    (f"  ({a.fail_actions} fail)", "red" if a.fail_actions else DIM),
                    ("\n", ""),
                )
            )

        # cost
        if a.cost_usd:
            lines.append(
                Text.assemble(
                    ("  ┃ ", DIM),
                    ("cost ", DIM),
                    (f"${a.cost_usd:.4f}", f"bold {_cost_color(a.cost_usd)}"),
                    ("\n", ""),
                )
            )

        # findings
        for f in (a.findings or [])[:4]:
            sev = str(f.get("severity", "info")).lower()
            lines.append(
                Text.assemble(
                    ("  ◆ ", SEV_STYLE.get(sev, "dim")),
                    (f.get("endpoint", "?"), f"bold {WHITE}"),
                    (f"  {f.get('description', '')[:65]}", DIM),
                    ("\n", ""),
                )
            )

        # recent actions
        if a.recent_actions:
            lines.append(Text.assemble(("  ── recent ──", f"dim {DIM}"), ("\n", "")))
            for act in a.recent_actions[:3]:
                lines.append(Text(f"  · {act}", style=DIM))

        return Panel(
            lines,
            title=f" [{GOLD}]{a.pid}[/{GOLD}] — detail ",
            border_style=GOLD,
            padding=(0, 0),
        )

    def _aggregate_panel(self) -> Panel:
        n = len(self.agents)
        cost = sum(a.cost_usd or 0 for a in self.agents)
        iters = sum(a.iteration for a in self.agents)
        all_finds: list[dict] = []
        for a in self.agents:
            all_finds.extend(a.findings or [])
        conf = sum(1 for f in all_finds if str(f.get("severity", "")).lower() in ("high", "critical"))
        med = sum(1 for f in all_finds if str(f.get("severity", "")).lower() == "medium")
        low = len(all_finds) - conf - med

        phases: dict[str, int] = {}
        for a in self.agents:
            phases[a.phase] = phases.get(a.phase, 0) + 1
        phase_str = Text()
        for i, (ph, cnt) in enumerate(sorted(phases.items(), key=lambda x: -x[1])):
            if i:
                phase_str.append("  ", style=DIM)
            style = PHASE_STYLE.get(ph, "white")
            phase_str.append(f"{cnt} {ph[:5]}", style=f"bold {style}")

        uptime = max((a.uptime for a in self.agents), default=0)

        body = Text()
        body.append(f"\n  {n}", style="bold white")
        body.append(f" agent{'s' if n != 1 else ''}\n", style=DIM)
        body.append(f"\n  ${cost:.3f}", style=f"bold {_cost_color(cost)}")
        body.append("  total cost\n", style=DIM)
        body.append(f"\n  {iters}", style="bold white")
        body.append("  iterations\n", style=DIM)
        body.append("\n  ")
        body.append(f"{conf}", style=f"bold {RED}" if conf else "dim")
        body.append(" high  ")
        body.append(f"{med}", style="bold yellow" if med else "dim")
        body.append(" med  ")
        body.append(f"{low}", style="dim")
        body.append(" low\n", style=DIM)
        body.append(f"\n  {phase_str}\n", style=DIM)
        body.append(f"\n  {_fmt_dur(uptime)}", style="dim italic")
        body.append("  longest uptime\n", style=DIM)

        return Panel(body, title=f" [{GOLD}]aggregate[/{GOLD}] ", border_style=DIM, padding=(0, 0))

    def _mesh_panel(self) -> Panel:
        lines = Text()
        if self.chat:
            for msg in self.chat[-8:]:
                # color the timestamp dim, the agent name bold
                lines.append(f"  {msg}\n", style=DIM)
        else:
            lines.append("  (no mesh activity)\n", style=DIM)
        return Panel(lines, title=f" [{GOLD}]mesh[/{GOLD}] ", border_style=DIM, padding=(0, 0))

    # ── layout ───────────────────────────────────────────────────────

    def _build(self) -> Layout:
        """Two-column layout: left (agents + detail), right (aggregate + mesh).
        Uses split_column then split_row to avoid 3-level nesting that Rich
        sometimes fails to populate."""
        layout = Layout(name="root")
        layout.split_column(
            Layout(name="top", size=2),
            Layout(name="main", ratio=1),
            Layout(name="bottom", size=1),
        )
        layout["main"].split_row(
            Layout(name="left", ratio=3),
            Layout(name="right", ratio=2),
        )
        layout["left"].split_column(
            Layout(name="agents", ratio=1),
            Layout(name="detail", ratio=1),
        )
        layout["right"].split_column(
            Layout(name="aggregate", ratio=1),
            Layout(name="mesh", ratio=1),
        )
        return layout

    def _render(self, layout: Layout) -> None:
        with self._lock:
            layout["top"].update(self._header())
            layout["agents"].update(self._agents_panel())
            layout["detail"].update(self._detail_panel())
            layout["aggregate"].update(self._aggregate_panel())
            layout["mesh"].update(self._mesh_panel())
            layout["bottom"].update(
                Text.assemble(
                    (" ↑↓/jk select · r refresh · q quit", "dim"),
                    (f"  {self.note}", "dim italic"),
                )
            )

    # ── main loop ────────────────────────────────────────────────────

    def run(self) -> None:
        self.start_poll()
        time.sleep(0.5)

        if not sys.stdin.isatty():
            self._render_once()
            return

        import select
        import termios
        import tty

        old = termios.tcgetattr(sys.stdin)
        try:
            tty.setcbreak(sys.stdin.fileno())
            layout = self._build()
            with Live(layout, console=self.console, refresh_per_second=4, screen=False):
                while self.running:
                    self._render(layout)
                    r, _, _ = select.select([sys.stdin], [], [], 0.25)
                    if r:
                        ch = sys.stdin.read(1)
                        self._key(ch)
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
            self.running = False

    def _render_once(self) -> None:
        layout = self._build()
        self._render(layout)
        self.console.print(layout)

    def _key(self, ch: str) -> None:
        if ch == "q":
            self.running = False
        elif ch in ("j", "\x1b[B"):
            with self._lock:
                self.selected = min(self.selected + 1, max(0, len(self.agents) - 1))
        elif ch in ("k", "\x1b[A"):
            with self._lock:
                self.selected = max(self.selected - 1, 0)
        elif ch == "r":
            self.note = "· refreshed"


# ═══════════════════════════════════════════════════════════════════════
# Entry
# ═══════════════════════════════════════════════════════════════════════


def main(demo: bool = False) -> int:
    if demo:
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="suijin_operator_demo_"))
        mesh, ws = tmp / "mesh", tmp / "workspace"
        write_demo(mesh, ws)
        tui = OperatorTUI(mesh, ws)
        print(f"  demo data: {tmp}")
        print("  press q to quit\n")
        tui.run()
        return 0

    tui = OperatorTUI(MESH_DIR, WORKSPACE_DIR)
    tui.run()
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(prog="operator", description="Suijin Operator — multi-agent command center")
    ap.add_argument("--demo", action="store_true", help="render with mock data (no live agents needed)")
    args = ap.parse_args()
    raise SystemExit(main(demo=args.demo))
