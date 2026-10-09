"""Suijin Console — the multi-agent command center.

Blueprint layout (terminal mapping of the operator spec):
  Row 1  status strip   — items left, live clock far right
  Row 2  agent cards    — exactly 6, each 1/6th of full width,
                          active card cyan border, inactive dark gray
  Row 3  middle (grow)  — Details pane 70% (left) | right column 30%:
                          stats strip on top + mesh visualizer below
  Row 4  console log    — scrolling feed, newest at the bottom,
                          footer shortcut strip at the very bottom edge

Data sources (all files the running system already writes):
  ~/.suijin/mesh/<pid>.json         — registry: pid, target, phase, beat, port
  ~/.suijin/mesh/<pid>-state.json   — digest: iteration, findings, footholds
  engagements/*/audit_trails/*.json  — full iterations, cost, findings
  ~/.suijin/mesh/gc.log             — coordination feed

`suijn operator` — live. `suijn operator --demo` — mock data.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from rich.align import Align
from rich.box import SQUARE
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

# ── palette (vibrant) ───────────────────────────────────────────────────
GOLD = "#ffb347"
CYAN = "#00e5ff"
RED = "#ff4444"
GREEN = "#00ff88"
MAGENTA = "#e040fb"
WHITE = "#ffffff"
DIM = "#b0b8c0"  # was #37474f — unreadable on dark, retired
GRAY = "#b0b8c0"

BLUE = "#7aa2f7"
ORANGE_L = "#ffa657"
C_CRIT = "#ff1744"
C_HIGH = "#ff6d00"
C_MED = "#ffea00"
C_LOW = "#69f0ae"

SEV_COLOR = {
    "critical": C_CRIT,
    "high": C_HIGH,
    "medium": C_MED,
    "low": C_LOW,
    "info": CYAN,
}

PHASE_COLOR = {
    "recon": CYAN,
    "informational": CYAN,
    "exploitation": "#ffd700",
    "post_exploitation": GREEN,
}
PULSE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

MESH_DIR = Path.home() / ".suijin" / "mesh"
WORKSPACE_DIR = Path.home() / ".suijin" / "workspace"
POLL_S = 0.2

VISIBLE_CARDS = 5

# ── the dragon (do not change; two frames, 0.25s each) ─────────────
DRAGON_FRAMES = (
    "\x1b[38;5;16m████\x1b[0m      \x1b[38;5;15m▄▀         ▀▄\x1b[0m\n\x1b[38;5;16m████\x1b[0m       \x1b[38;5;15m██       ██\x1b[0m\n\x1b[38;5;16m████\x1b[0m     \x1b[38;5;44m▄█████████████▄\x1b[0m\n\x1b[38;5;16m████\x1b[0m    \x1b[38;5;44m███\x1b[38;5;16m█\x1b[38;5;44m██████\x1b[38;5;16m█\x1b[38;5;44m█████\x1b[0m\n\x1b[38;5;16m████\x1b[0m    \x1b[38;5;44m█████████████████\x1b[38;5;121m▄▀\x1b[0m\n\x1b[38;5;16m████\x1b[0m    \x1b[38;5;44m▀████\x1b[38;5;16m▄▄▄\x1b[38;5;44m█████████\x1b[38;5;121m█\x1b[38;5;44m▄\x1b[0m\n         \x1b[38;5;44m▀███\x1b[38;5;211m\x1b[48;5;44m▀▀▀\x1b[0m\x1b[38;5;44m████████████\x1b[0m\n           \x1b[38;5;44m███████████████▀\x1b[0m\n           \x1b[38;5;44m█▀█   █▀  ▀██\x1b[0m\n          \x1b[38;5;239m████████████\x1b[0m",
    "\x1b[38;5;16m████\x1b[0m      \x1b[38;5;15m▄▀         ▀▄\x1b[0m\n\x1b[38;5;16m████\x1b[0m       \x1b[38;5;15m██       ██\x1b[0m\n\x1b[38;5;16m████\x1b[0m     \x1b[38;5;44m▄█████████████▄\x1b[0m\n\x1b[38;5;16m████\x1b[0m    \x1b[38;5;44m███\x1b[38;5;16m█\x1b[38;5;44m██████\x1b[38;5;16m█\x1b[38;5;44m█████\x1b[0m\n\x1b[38;5;16m████\x1b[0m    \x1b[38;5;44m█████████████████\x1b[38;5;121m▄▀\x1b[0m\n\x1b[38;5;16m████\x1b[0m    \x1b[38;5;44m▀████\x1b[38;5;16m▄▄▄\x1b[38;5;44m█████████\x1b[38;5;121m█\x1b[38;5;44m▄\x1b[0m\n         \x1b[38;5;44m▀███\x1b[38;5;211m\x1b[48;5;44m▀▀▀\x1b[0m\x1b[38;5;44m████████████\x1b[0m\n           \x1b[38;5;44m███████████████▀\x1b[0m\n            \x1b[38;5;44m▀█  █▀█  ▀██\x1b[0m\n          \x1b[38;5;239m████████████\x1b[0m",
)

CYBER_QUOTES = (
    "The network forgets nothing. Walk like you belong.",
    "Quiet access outlives loud exploits.",
    "Every door is a question. Ask politely, then pick the lock.",
    "Ghost in, ghost out — the logs remember ghosts differently.",
    "Root is a state of mind. Then it's a shell.",
    "Recon is patience with intent.",
    "The best payload is the one that looks like traffic.",
    "Persist where they don't look. They never look.",
    "Credentials are keys people tape under doormats.",
    "Trust the noise. Hide inside it.",
    "A closed port is just an opinion.",
    "Exploit the architecture, not the patch level.",
    "Move at the speed of no alarms.",
    "The scanner finds the door. Thinking finds the house.",
    "Silence is a tool. Use it sparingly.",
    "Loot what matters. Leave no receipts.",
    "Their perimeter is a promise, not a wall.",
    "Every misconfiguration is an invitation RSVP'd.",
    "Be the anomaly they mistake for baseline.",
    "Firewalled? The web app is the firewall's mouth.",
    "Escalate quietly. Privilege loves company.",
    "The chain beats the link. Build chains.",
    "Shell today, persistence tomorrow, legend by Friday.",
    "Nothing is unhackable. Somethings are just unfunded.",
    "Enumerate until the target tells you its story.",
    "Blend in. Stand out only when exfiltrating.",
    "The best tunnel looks like a plumbing problem.",
    "Session spawned is sovereignty asserted.",
    "Patch Tuesday is loot Monday.",
    "Live in the gaps between their alerts.",
)


# screen rows (1-indexed): 1 = 00, 2-6 = 01, and two more rows extend the
# screen DOWN beside the dragon's body (row 7 = 01, row 8 = the 00 cap) —
# 0 is a black cell, 1 is white; each cell is two blocks. The dragon's own
# characters never move: the new cells occupy the empty left margin.
DRAGON_SCREEN_LIT_ROWS = (2, 3, 4, 5, 6)


def _dragon() -> Text:
    """Frame from the wall clock — 0.25s per frame, 2-frame loop.
    no_wrap keeps the art on its own lines — the panel crops whatever
    exceeds its width, so the dragon always stays inside the panel.
    The big screen: 00 / 01 x6 / 00. Rows 2-6 light the tower's right
    half; rows 7-8 EXTEND the screen down into the empty margin beside
    the dragon's body — every dragon character stays exactly where it
    is. Raw frames untouched; transform happens at render time."""
    idx = int(time.time() * 4) % 2
    t = _DRAGON_CACHE.get(idx)
    if t is None:
        lines = DRAGON_FRAMES[idx].split("\n")
        out = []
        for i, ln in enumerate(lines, start=1):
            if i in DRAGON_SCREEN_LIT_ROWS:
                ln = ln.replace("\x1b[38;5;16m████", "\x1b[38;5;16m██\x1b[38;5;15m██", 1)
            elif i == 7:  # screen keeps growing beside the body (01 row)
                ln = "\x1b[38;5;16m██\x1b[38;5;15m██\x1b[0m" + ln[4:]
            elif i == 8:  # the black cap row of the extended screen (00)
                ln = "\x1b[38;5;16m████\x1b[0m" + ln[4:]
            out.append(ln)
        t = Text.from_ansi("\n".join(out))
        t.no_wrap = True
        _DRAGON_CACHE[idx] = t
    return t


# ── the server rack (do not change; two frames, 0.25s each) ──────────────
RACK_FRAMES = (
    "\x1b[38;5;237m▄▄▄▄▄▄▄▄▄▄▄▄▄▄\x1b[0m\n\x1b[38;5;237m█ \x1b[38;5;234m█████████\x1b[38;5;46m■\x1b[38;5;234m█\x1b[38;5;196m■\x1b[38;5;237m █\x1b[0m\n\x1b[38;5;237m█ \x1b[38;5;234m█████████\x1b[38;5;232m■\x1b[38;5;234m█\x1b[38;5;196m■\x1b[38;5;237m █\x1b[0m\n\x1b[38;5;237m█ \x1b[38;5;234m█████████\x1b[38;5;46m■\x1b[38;5;234m█\x1b[38;5;232m■\x1b[38;5;237m █\x1b[0m\n\x1b[38;5;237m█ \x1b[38;5;234m█████████\x1b[38;5;232m■\x1b[38;5;234m█\x1b[38;5;196m■\x1b[38;5;237m █\x1b[0m\n\x1b[38;5;237m█ \x1b[38;5;234m█████████\x1b[38;5;46m■\x1b[38;5;232m█\x1b[38;5;232m■\x1b[38;5;237m █\x1b[0m\n\x1b[38;5;237m█ \x1b[38;5;234m█████████\x1b[38;5;232m■\x1b[38;5;234m█\x1b[38;5;196m■\x1b[38;5;237m █\x1b[0m\n\x1b[38;5;237m▀▀▀▀▀▀▀▀▀▀▀▀▀▀\x1b[0m",
    "\x1b[38;5;237m▄▄▄▄▄▄▄▄▄▄▄▄▄▄\x1b[0m\n\x1b[38;5;237m█ \x1b[38;5;234m█████████\x1b[38;5;232m■\x1b[38;5;234m█\x1b[38;5;232m■\x1b[38;5;237m █\x1b[0m\n\x1b[38;5;237m█ \x1b[38;5;234m█████████\x1b[38;5;46m■\x1b[38;5;234m█\x1b[38;5;196m■\x1b[38;5;237m █\x1b[0m\n\x1b[38;5;237m█ \x1b[38;5;234m█████████\x1b[38;5;232m■\x1b[38;5;234m█\x1b[38;5;196m■\x1b[38;5;237m █\x1b[0m\n\x1b[38;5;237m█ \x1b[38;5;234m█████████\x1b[38;5;46m■\x1b[38;5;234m█\x1b[38;5;232m■\x1b[38;5;237m █\x1b[0m\n\x1b[38;5;237m█ \x1b[38;5;234m█████████\x1b[38;5;232m■\x1b[38;5;234m█\x1b[38;5;196m■\x1b[38;5;237m █\x1b[0m\n\x1b[38;5;237m█ \x1b[38;5;234m█████████\x1b[38;5;46m■\x1b[38;5;232m█\x1b[38;5;232m■\x1b[38;5;237m █\x1b[0m\n\x1b[38;5;237m▀▀▀▀▀▀▀▀▀▀▀▀▀▀\x1b[0m",
)


def _rack() -> Text:
    """Rack LEDs, wall-clock frame at 0.25s — sits right of the dragon."""
    idx = int(time.time() * 4) % 2
    t = _RACK_CACHE.get(idx)
    if t is None:
        t = Text.from_ansi(RACK_FRAMES[idx])
        t.no_wrap = True
        _RACK_CACHE[idx] = t
    return t


def _mascots() -> Table:
    """Sui (the dragon) with the server rack at its side — nudged
    4 cells right and 3 down from the dragon's top-left."""
    g = Table.grid(padding=(0, 3))
    g.add_column()
    g.add_column()
    g.add_row(_dragon(), Padding(_rack(), (3, 4, 0, 0)))
    return g


def _quote() -> Text:
    """One cyber quote, orange, rotates every 15 minutes."""
    q = CYBER_QUOTES[int(time.time() // 900) % len(CYBER_QUOTES)]
    t = Text(f"\n {q}", style=f"bold italic {ORANGE_L}")
    t.no_wrap = True
    return t


# 6th cell in the row is the summary/stats block

VERSION_URL = "https://raw.githubusercontent.com/0xwi11iam/Suijin/main/suijin/version.json"
_version = {"label": None}


def _fetch_version() -> None:
    """Live version from the gh repo's version.json; 'version not found'
    when unreachable. Cached once per console session."""
    try:
        with urllib.request.urlopen(VERSION_URL, timeout=5) as r:
            data = json.loads(r.read().decode())
        v = str(data.get("version") or "").strip()
        _version["label"] = f"Suijin v{v} release" if v else "version not found"
    except Exception:
        _version["label"] = "version not found"


def _version_label() -> str:
    return _version["label"] or "version not found"


# engagement-side commands available while su'd INTO an agent (/su <hex>);
# they act on that agent's engagement through its files — the same
# surfaces the in-window pause console uses
AGENT_COMMANDS = {
    "/state": "agent state summary",
    "/audit": "audit trail summary",
    "/cost": "spend so far",
    "/findings": "confirmed exploits",
    "/note <text>": "write an engagement note",
    "/objective <text>": "redirect the agent (read next turn)",
    "/panic": "hold the agent now",
    "/su root": "back to the root operator",
}

COMMANDS = {
    "/next": "select next agent",
    "/prev": "select previous agent",
    "/agent <hex>": "select agent by hex id",
    "/focus <area>": "focus cards|details|overview|mesh|input",
    "/say <msg>": "broadcast to the mesh as the operator",
    "/exploits": "list confirmed exploits in Details",
    "/tail <hex>": "select agent and follow its live detail",
    "/sort <field>": "cards by iter|cost|finds|uptime",
    "/filter <phase>": "cards by phase; all clears",
    "/pause [msg]": "pause all agents; optional message they read on resume",
    "/resume": "release paused agents",
    "/su <hex>": "operate as an agent (root returns)",
    "/clear": "clear the chat display",
    "/refresh": "force an immediate rescan",
    "/quit": "exit the console",
}


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
        "last_reasoning",
        "last_tool",
        "last_args",
        "last_ok",
        "last_chain",
        "last_obs",
        "iterations",
        "system_one",
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
        return sum(
            1
            for f in (self.findings or [])
            if isinstance(f, dict) and str(f.get("severity", "")).lower() in ("high", "critical")
        )

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


def _confirmed_exploits(eng_dir) -> list[dict]:
    """Machine-proven exploits ONLY: the POC verifier's CONFIRMED catalog
    entries (exploits/*/EXP-*/description.md). Agent-claimed audit findings
    (behavior notes, false positives, unproven vulns) do not count."""
    out: list[dict] = []
    try:
        mds = sorted(Path(eng_dir).glob("exploits/*/EXP-*/description.md")) if eng_dir else []
    except Exception:
        mds = []
    for md in mds:
        try:
            text = md.read_text(errors="replace")
        except OSError:
            continue
        if "status: **CONFIRMED**" not in text:
            continue
        sev_m = re.search(r"severity \(SYSTEM\): \*\*(\w+)\*\*", text)
        cls_m = re.search(r"class: \*\*([\w-]+)\*\*", text)
        title_m = re.search(r"^# (EXP-\d+ \u2014 .+)$", text, re.M)
        out.append(
            {
                "severity": (sev_m.group(1).lower() if sev_m else "info"),
                "type": (cls_m.group(1) if cls_m else "exploit"),
                "endpoint": md.parent.name,
                "description": (title_m.group(1) if title_m else md.parent.name),
            }
        )
    return out


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def scan_agents(mesh_dir: Path, workspace: Path, live: bool = True) -> list[Agent]:
    """Mesh nodes, enriched with audit-trail detail. Live mode drops (and
    prunes the files of) processes that no longer exist — a closed window
    is gone. Demo mode trusts the registry as written."""
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
        started = float(rec.get("started") or 0)
        gone = (live and not _pid_alive(pid)) or (started and time.time() - started > 86400)
        if gone:
            # the window closed (or the node outlived a day — pid recycling
            # can fake liveness) — the node is gone, take its files too
            with contextlib.suppress(OSError):
                (mesh_dir / f"{pid}.json").unlink(missing_ok=True)
                (mesh_dir / f"{pid}-state.json").unlink(missing_ok=True)
            continue
        beat = float(rec.get("beat") or 0)
        # stale beats no longer DROP the agent — the info stays on screen
        # (alive property flips to False at 30s: pulse goes ✗, numbers hang)

        state = _rj(mesh_dir / f"{pid}-state.json")
        a = Agent(
            pid=pid,
            target=str(rec.get("summary") or "?")[:50],
            phase=str(state.get("phase") or rec.get("phase") or "?"),
            iteration=int(state.get("iteration") or 0),
            findings=state.get("findings") or [],
            system_one=state.get("system_one") or None,
            footholds=state.get("footholds") or [],
            recent_actions=[str(x) for x in (state.get("recent_actions") or [])],
            beat=beat,
            port=int(rec.get("port") or 0),
            started=float(rec.get("started") or 0),
        )

        # match audit EXACTLY by engagement dir when the digest carries it;
        # fall back to closest-started (within 60s) only for old sessions
        hint = str(state.get("engagement_dir") or "").rstrip("/")
        if hint:
            for ts, data, eng_dir in audits:
                if str(eng_dir).rstrip("/") == hint:
                    a.audit_data = data
                    a.engagement_dir = eng_dir
                    break
        if a.audit_data is None:
            best_gap = 999
            for ts, data, eng_dir in audits:
                gap = abs(ts - a.started)
                if gap < min(best_gap, 60):
                    best_gap = gap
                    a.audit_data = data
                    a.engagement_dir = eng_dir
        # findings = machine-proven only (POC-confirmed catalog entries);
        # digest titles and audit claims stay out of every count
        a.findings = _confirmed_exploits(a.engagement_dir)

        if a.audit_data:
            d = a.audit_data
            a.cost_usd = float(d.get("cost_usd") or 0)
            a.total_actions = int(d.get("total_actions") or 0)
            a.ok_actions = int(d.get("successful_actions") or 0)
            a.fail_actions = int(d.get("failed_actions") or 0)
            iters = d.get("iterations") or []
            a.iterations = iters[-20:]
            if iters:
                last = iters[-1]
                a.last_thought = str(last.get("thought") or "")
                a.last_reasoning = str(last.get("reasoning") or "")
                act = last.get("action") or {}
                a.last_tool = str(act.get("tool") or "?")
                a.last_args = act.get("args") or {}
                a.last_ok = bool(act.get("success", True))
                a.last_obs = str(last.get("observation") or "")
                a.last_chain = str(last.get("chain_context") or "")
            # findings stay confirmed-exploits-only — audit claims
            # (behavior notes, false positives) never backfill the count

        agents.append(a)

    agents.sort(key=lambda x: -x.iteration)
    return agents


def read_chat(mesh_dir: Path, limit: int = 12, skip_bytes: int = 0) -> tuple[list[str], int]:
    """Tail of the mesh chat. skip_bytes>0 reads only NEW bytes (the
    operator cleared the view — old lines stay suppressed). Returns
    (lines, new_offset)."""
    p = mesh_dir / "gc.log"
    if not p.is_file():
        return [], skip_bytes
    try:
        size = p.stat().st_size
        if skip_bytes and size >= skip_bytes:
            with p.open("rb") as fh:
                fh.seek(skip_bytes)
                text = fh.read().decode("utf-8", "replace")
            lines = text.splitlines()
        else:
            lines = p.read_text(errors="replace").splitlines()
    except OSError:
        return [], skip_bytes
    out = []
    for ln in lines[-max(limit, 20) :] if not skip_bytes else lines:
        parts = ln.split("|", 3)
        if len(parts) == 4:
            ts, pid, name, msg = parts
            out.append(f"{ts} [{name}:{pid}] {msg}")
    return out, size


# ═══════════════════════════════════════════════════════════════════════
# Mock data
# ═══════════════════════════════════════════════════════════════════════


def write_demo(mesh: Path, ws: Path) -> None:
    mesh.mkdir(parents=True, exist_ok=True)
    now = time.time()

    specs = [
        {
            "pid": 91001,
            "poc": {
                "id": "EXP-001",
                "title": "Upload traversal to credential store",
                "cls": "path_traversal",
                "sev": "high",
                "ok": True,
            },
            "phase": "post_exploitation",
            "iter": 34,
            "cost": 0.019,
            "thought": "Credential grid exhausted — pivoting to /files/ upload paths",
            "reasoning": "The /files/ upload surface accepted the last two payloads; if traversal is possible there, stored credentials are reachable without another auth bypass. Checking upload paths before spending more iterations on the auth layer.",
            "tool": "http_request",
            "args": {"method": "GET", "path": "/files/../../etc/credentials"},
            "chain": " foothold → credential access (in progress)",
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
            "poc": {
                "id": "EXP-002",
                "title": "JWT alg confusion forge",
                "cls": "jwt_confusion",
                "sev": "critical",
                "ok": True,
            },
            "phase": "exploitation",
            "iter": 20,
            "cost": 0.014,
            "thought": "Auth layer validates tokens — testing alg confusion",
            "reasoning": "Tokens are validated, but the JWKS endpoint publishes the public cert. If the verifier accepts HS256 keyed with that same cert, I can mint admin tokens. One targeted test settles it.",
            "tool": "execute_terminal",
            "args": {"cmd": "python3 forge_token.py --alg HS256"},
            "chain": " recon → jwt forge (verified)",
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
            "thought": "Fingerprinting edge gateway → FastAPI core-api",
            "reasoning": "Response headers and error shapes point to a thin proxy over a FastAPI core. Mapping the route tree first — blind probing through the edge wastes calls.",
            "tool": "http_request",
            "args": {"method": "GET", "path": "/"},
            "chain": "",
            "obs": "Status: 200 — root serves marketing HTML",
            "ok": 12,
            "fail": 0,
            "started_offset": 240,
            "finds": [],
        },
        {
            "pid": 91004,
            "poc": {
                "id": "EXP-003",
                "title": "Webhook SSRF to internal auth",
                "cls": "ssrf",
                "sev": "high",
                "ok": True,
            },
            "phase": "exploitation",
            "iter": 28,
            "cost": 0.016,
            "thought": "Testing SSRF via webhook registration",
            "reasoning": "Webhook endpoints fetch attacker URLs server-side. If the fetcher follows redirects to internal hosts, the auth service is reachable from inside.",
            "tool": "http_request",
            "args": {"method": "POST", "path": "/api/v1/webhooks"},
            "chain": " recon → ssrf probe",
            "obs": "Status: 200 — internal auth service reachable",
            "ok": 25,
            "fail": 3,
            "started_offset": 600,
            "finds": [
                {
                    "severity": "high",
                    "type": "ssrf",
                    "endpoint": "/api/v1/webhooks",
                    "description": "Webhook URL fetches internal endpoints",
                }
            ],
        },
        {
            "pid": 91005,
            "phase": "recon",
            "iter": 8,
            "cost": 0.005,
            "thought": "Mapping /api/v1 route tree",
            "reasoning": "Noun-route enumeration first; verbs follow. The tree tells me where auth is thin before I spend payloads.",
            "tool": "surface_expand",
            "args": {"root": "/api/v1"},
            "chain": "",
            "obs": "mapped 6 surfaces under /api/v1",
            "ok": 8,
            "fail": 0,
            "started_offset": 120,
            "finds": [],
        },
        {
            "pid": 91006,
            "poc": {"id": "EXP-004", "title": "SSTI command execution", "cls": "ssti", "sev": "critical", "ok": True},
            "phase": "post_exploitation",
            "iter": 45,
            "cost": 0.025,
            "thought": "Exfiltrating credential grid via SSRF chain",
            "reasoning": "The SSRF chain reached the credential service; pulling the grid now while the session token is still valid.",
            "tool": "http_request",
            "args": {"method": "GET", "path": "/internal/credentials"},
            "chain": " ssrf → credential access (verified)",
            "obs": "200 — data exfil confirmed",
            "ok": 42,
            "fail": 3,
            "started_offset": 1800,
            "finds": [
                {
                    "severity": "critical",
                    "type": "rce",
                    "endpoint": "/jobs",
                    "description": "SSTI in template field — command execution",
                },
                {
                    "severity": "high",
                    "type": "ssrf",
                    "endpoint": "/api/v1/webhooks",
                    "description": "Internal SSRF to auth service",
                },
                {
                    "severity": "low",
                    "type": "info_disclosure",
                    "endpoint": "/metrics",
                    "description": "Internal metrics exposed",
                },
            ],
        },
        {
            "pid": 91007,
            "phase": "recon",
            "iter": 5,
            "cost": 0.003,
            "thought": "Crawling docs portal for API schemas",
            "reasoning": "Public docs often leak the private schema. One GET beats ten guesses.",
            "tool": "http_request",
            "args": {"method": "GET", "path": "/docs"},
            "chain": "",
            "obs": "Status: 200 — openapi.json linked",
            "ok": 5,
            "fail": 0,
            "started_offset": 90,
            "finds": [],
        },
        {
            "pid": 91008,
            "phase": "exploitation",
            "iter": 17,
            "cost": 0.011,
            "thought": "Probing IDOR on invoice endpoints",
            "reasoning": "Tenant checks that miss on external invoices are the classic gap here. Sequential IDs make verification cheap.",
            "tool": "http_request",
            "args": {"method": "GET", "path": "/api/v1/invoices/EXT-0042"},
            "chain": "",
            "obs": "Status: 200 — cross-tenant read",
            "ok": 15,
            "fail": 2,
            "started_offset": 320,
            "finds": [
                {
                    "severity": "medium",
                    "type": "idor",
                    "endpoint": "/api/v1/invoices/EXT-*",
                    "description": "External invoices skip tenant check",
                }
            ],
        },
        {
            "pid": 91009,
            "poc": {"id": "EXP-005", "title": "Token forge retry", "cls": "jwt_confusion", "sev": "high", "ok": False},
            "phase": "exploitation",
            "iter": 22,
            "cost": 0.014,
            "thought": "Forging admin tokens via key confusion",
            "reasoning": "The JWKS publishes the RSA public key. If the verifier accepts HS256 with that key, admin tokens are mintable offline.",
            "tool": "execute_terminal",
            "args": {"cmd": "python3 forge_token.py --alg HS256"},
            "chain": " recon → jwt forge",
            "obs": "python3: HS256 with cert PEM accepted",
            "ok": 20,
            "fail": 2,
            "started_offset": 460,
            "finds": [
                {
                    "severity": "critical",
                    "type": "jwt_confusion",
                    "endpoint": "/api/v1/me",
                    "description": "HS256 keyed with public cert — forgeable tokens",
                }
            ],
        },
        {
            "pid": 91010,
            "phase": "recon",
            "iter": 3,
            "cost": 0.002,
            "thought": "Baseline fingerprinting the edge CDN",
            "reasoning": "Cache behavior first — a poisoned cache multiplies everything downstream.",
            "tool": "http_request",
            "args": {"method": "HEAD", "path": "/"},
            "chain": "",
            "obs": "x-cache: HIT — candidate for poisoning",
            "ok": 3,
            "fail": 0,
            "started_offset": 45,
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
        eng = ws / "engagements" / f"demo_{s['pid']}_127_0_0_1_6000"
        (mesh / f"{s['pid']}-state.json").write_text(
            json.dumps(
                {
                    "pid": s["pid"],
                    "phase": s["phase"],
                    "iteration": s["iter"],
                    "findings": s["finds"],
                    "footholds": [],
                    "engagement_dir": str(eng),
                    "system_one": {
                        "engine": "laya-mlx",
                        "status": "loaded",
                        "decisions": s["iter"] // 3,
                        "fallbacks": 1 if s["pid"] % 4 == 0 else 0,
                        "ms_avg": 8 + s["pid"] % 7,
                    },
                    "recent_actions": [
                        f"{s['tool']}: {s['thought'][:55]}",
                        "http_request: probing /api/v1/ping → 200",
                        "surface_expand: mapped /api/v1/* noun routes",
                    ],
                }
            )
        )
        # machine-proven exploits: the console counts ONLY these
        if s.get("poc"):
            exp = eng / "exploits" / "127_0_0_1_6000" / s["poc"]["id"]
            exp.mkdir(parents=True, exist_ok=True)
            st = "CONFIRMED" if s["poc"]["ok"] else "FAILED_TO_CONFIRM"
            (exp / "description.md").write_text(
                f"# {s['poc']['id']} — {s['poc']['title']}\n\n"
                f"- class: **{s['poc']['cls']}**\n"
                f"- target: `127.0.0.1:6000`\n"
                f"- status: **{st}**\n"
                f"- severity (SYSTEM): **{s['poc']['sev'].upper()}**\n"
            )
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
                            "iteration": s["iter"] - 2,
                            "timestamp": iso,
                            "phase": "recon",
                            "thought": "Mapping attack surface around the gateway",
                            "reasoning": s["reasoning"],
                            "action": {"tool": "surface_expand", "args": {"root": "/api/v1"}, "success": True},
                            "observation": "mapped 6 surfaces",
                            "chain_context": "",
                            "completion_reason": "",
                        },
                        {
                            "iteration": s["iter"] - 1,
                            "timestamp": iso,
                            "phase": s["phase"],
                            "thought": "Narrowing to the highest-value endpoints found so far",
                            "reasoning": s["reasoning"],
                            "action": {"tool": s["tool"], "args": s.get("args", {}), "success": True},
                            "observation": "Status: 200 — candidate confirmed",
                            "chain_context": s.get("chain", ""),
                            "completion_reason": "",
                        },
                        {
                            "iteration": s["iter"],
                            "timestamp": iso,
                            "phase": s["phase"],
                            "thought": s["thought"],
                            "reasoning": s["reasoning"],
                            "action": {"tool": s["tool"], "args": s.get("args", {}), "success": True},
                            "observation": s["obs"],
                            "chain_context": s.get("chain", ""),
                            "completion_reason": "",
                        },
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
        ts = datetime.fromtimestamp(now - s["started_offset"] + 60, tz=timezone.utc).strftime("%H:%M:%S")
        chat_lines.append(f"{ts}|{s['pid']}|agent|{s['thought'][:70]}")
    (mesh / "gc.log").write_text("\n".join(chat_lines) + "\n")


# ═══════════════════════════════════════════════════════════════════════
# Rendering
# ═══════════════════════════════════════════════════════════════════════

_frame = {"i": 0}
_DRAGON_CACHE: dict = {}
_RACK_CACHE: dict = {}


def _pulse() -> str:
    """Global spinner — every instance ticks together, 20 frames/s."""
    return PULSE_FRAMES[int(time.time() * 20) % len(PULSE_FRAMES)]


def _phase_label(phase: str) -> str:
    """Display name: the engagement's 'informational' reads as 'recon'."""
    return "recon" if str(phase).lower() == "informational" else str(phase)


def _hex_id(pid: int) -> str:
    return f"{pid:06x}"


def _cost_color(cost: float) -> str:
    if cost >= 0.50:
        return f"bold {RED}"
    if cost >= 0.20:
        return f"bold {C_HIGH}"
    return f"bold {GREEN}"


def _fmt_dur(seconds: float) -> str:
    m = int(seconds // 60)
    if m >= 60:
        return f"{m // 60}h{m % 60:02d}m"
    s = int(seconds % 60)
    return f"{m:02d}m{s:02d}s"


def dedup_findings(findings: list) -> list:
    """One finding per (type, endpoint) — or per title for digest strings."""
    seen: set = set()
    out = []
    for f in findings or []:
        if isinstance(f, dict):
            key = (str(f.get("type") or ""), str(f.get("endpoint") or ""), str(f.get("description") or "")[:60])
        else:
            key = ("title", str(f), "")
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _sev_counts(findings: list[dict]) -> dict[str, int]:
    out = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings or []:
        sev = str(f.get("severity", "info")).lower() if isinstance(f, dict) else "info"
        if sev in out:
            out[sev] += 1
    return out


def _card(a: Agent, selected: bool, height: int) -> Panel:
    body = Text()
    body.append(f" {_pulse() if a.alive else '✗'} ", style=f"bold {GREEN if a.alive else RED}")
    body.append(f"Agent {_hex_id(a.pid)}", style=f"bold {CYAN}")

    body.append(f"\n {_fmt_dur(a.uptime)}", style=f"italic {GREEN}")
    body.append(f"\n {a.iteration} iters", style=f"bold {C_HIGH}")

    pc = PHASE_COLOR.get(a.phase, WHITE)
    body.append(f"\n {_phase_label(a.phase)}", style=f"bold {pc}")

    sc = _sev_counts(a.findings)

    def seg(count: int, label: str, sev: str, tail: str = " ") -> None:
        col = SEV_COLOR[sev]
        body.append(f"{count} ", style=f"bold {col}")
        body.append(label + tail, style=col)

    body.append("\n ")
    seg(sc["low"], "LOW", "low")
    seg(sc["medium"], "MED", "medium")
    seg(sc["high"], "HIGH", "high", "")
    body.append("\n ")
    seg(sc["critical"], "CRIT", "critical", "")

    body.append(
        f"\n ${a.cost_usd:.3f}" if a.cost_usd else "\n —",
        style=_cost_color(a.cost_usd or 0) if a.cost_usd else DIM,
    )

    return Panel(
        Align(body, vertical="middle"),
        border_style=f"bold {CYAN}" if selected else WHITE,
        padding=(0, 1),
        height=height,
    )

    return Panel(
        Align(body, vertical="middle"),
        border_style=f"bold {CYAN}" if selected else WHITE,
        padding=(0, 1),
        height=height,
    )


class OperatorTUI:
    # focus ring follows the screen perimeter: cards (top-left) down the
    # left side (detail, input), up the right side (mesh, overview), wrap
    AREAS = ["cards", "detail", "input", "mesh", "overview"]
    AREA_COLOR = {"cards": CYAN, "detail": CYAN, "overview": CYAN, "mesh": CYAN, "input": CYAN}

    def __init__(self, mesh_dir: Path, workspace: Path, live: bool = True):
        self.mesh_dir = mesh_dir
        self.workspace = workspace
        self.live = live
        self.console = Console(force_terminal=True, highlight=False)
        self.agents: list[Agent] = []
        self.chat: list[str] = []
        self.selected = 0
        self.scroll = 0
        self.focus = self.AREAS.index("input")
        self.running = True
        self.note = ""
        self.note_at = 0.0
        self.sort_key = "iter"
        self.filter_phase: str | None = None
        self.exploits_view = False
        self.su_pid: int | None = None  # /su <hex>: input acts as this agent
        self._boot_t0 = 0.0
        self.iter_pos = 0  # detail history: 0 = newest, +1 = one older
        threading.Thread(target=_fetch_version, daemon=True, name="version-fetch").start()
        self.input_buf = ""
        self.history: list[str] = []
        self.hist_idx: int | None = None
        self.sug_sel = 0
        self._chat_offset = 0
        self._lock = threading.Lock()

    def start_poll(self) -> None:
        def _poll():
            while self.running:
                with contextlib.suppress(Exception):
                    self._poll_once()
                time.sleep(POLL_S)

        threading.Thread(target=_poll, daemon=True, name="operator-poll").start()

    def _poll_once(self) -> None:
        agents = scan_agents(self.mesh_dir, self.workspace, live=self.live)
        agents = self._filter_agents(self._sort_agents(agents))
        chat, offset = read_chat(self.mesh_dir, skip_bytes=self._chat_offset)
        with self._lock:
            self.agents = agents
            # incremental reads APPEND — the first read (offset 0) tails
            # the log; later reads deliver only NEW lines, and replacing
            # the list with them made the whole panel flash and vanish
            # (the live field-run bug: "says something and disappears")
            if self._chat_offset and chat:
                self.chat = (self.chat + chat)[-60:]
            elif chat:
                self.chat = chat
            self._chat_offset = offset
            if self.selected >= len(agents):
                self.selected = max(0, len(agents) - 1)

    def _note(self, msg: str) -> None:
        self.note = msg
        self.note_at = time.time()

    def _sort_agents(self, agents: list) -> list:
        if self.sort_key == "cost":
            return sorted(agents, key=lambda a: -(a.cost_usd or 0))
        if self.sort_key == "finds":
            return sorted(agents, key=lambda a: -len(a.findings or []))
        if self.sort_key == "uptime":
            return sorted(agents, key=lambda a: a.started or 0)  # longest-lived first
        return sorted(agents, key=lambda a: -(a.iteration or 0))

    def _filter_agents(self, agents: list) -> list:
        p = self.filter_phase
        if not p or p == "all":
            return agents
        return [a for a in agents if _phase_label(a.phase) == p]

    # ── panel renderers ─────────────────────────────────────────────

    def _focused(self, area: str) -> bool:
        return self.AREAS[self.focus] == area

    def _sev_strip(self, findings: list[dict]) -> Text:
        sc = _sev_counts(findings)
        total = sum(sc.values())
        t = Text()
        t.append(f"{sc['low']} low", style=f"bold {C_LOW}")
        t.append(" | ", style=DIM)
        t.append(f"{sc['medium']} med", style=f"bold {C_MED}")
        t.append(" | ", style=DIM)
        t.append(f"{sc['high']} high", style=f"bold {C_HIGH}")
        t.append(" | ", style=DIM)
        t.append(f"{sc['critical']} critical", style=f"bold {C_CRIT}")
        t.append(" | ", style=DIM)
        t.append(f"{total} total", style=f"bold {WHITE}")
        return t

    def _all_dict_findings(self) -> list[dict]:
        """Audited (severity-classified) findings across agents, deduped —
        digest title-strings don't count until they land in an audit."""
        raw: list = []
        for a in self.agents:
            raw.extend(a.findings or [])
        return [f for f in dedup_findings(raw) if isinstance(f, dict)]

    def _header(self) -> Panel:
        n = len(self.agents)
        cost = sum(a.cost_usd or 0 for a in self.agents)
        all_finds = self._all_dict_findings()

        left = Text()
        left.append(f" {_pulse()} ", style=f"bold {GOLD}")
        left.append("Console", style=f"bold {GOLD}")
        left.append("  |  ", style=DIM)
        left.append(f"{n}", style=f"bold {WHITE}")
        left.append(" agents", style=GRAY)
        left.append("  |  ", style=DIM)
        left.append(f"${cost:.2f}", style=_cost_color(cost))
        left.append(" | ", style=DIM)
        left.append(self._sev_strip(all_finds))
        with contextlib.suppress(OSError):
            pf = self.mesh_dir / "pause-operator"
            if pf.exists():
                age = max(0.0, time.time() - pf.stat().st_mtime)
                h = int(age // 3600)
                age_s = f"{h}h" if h else f"{int(age // 60)}m"
                left.append(f"  |  PAUSED {age_s}", style=f"bold {C_MED}")

        now = datetime.now().astimezone()
        off = now.strftime("%z")
        clock = Text(f"UTC {off[:3]}:{off[3:]} {now:%H:%M:%S} ", style=f"bold {CYAN}")

        grid = Table.grid(expand=True, pad_edge=False)
        grid.add_column(ratio=1)
        grid.add_column()
        grid.add_column(justify="right")
        grid.add_row(left, Text(f"{_version_label()} | ", style=WHITE), clock)

        return Panel(grid, border_style=WHITE, padding=(0, 0))

    def _agents_panel(self) -> Panel:
        if not self.agents:
            empty = Text("\n  (no live agents — start engagements)\n", style=DIM)
            return Panel(
                empty,
                border_style=f"bold {CYAN}" if self._focused("cards") else WHITE,
                padding=(0, 0),
            )

        # keep the selected card in view
        if self.selected < self.scroll:
            self.scroll = self.selected
        if self.selected >= self.scroll + VISIBLE_CARDS:
            self.scroll = self.selected - VISIBLE_CARDS + 1
        self.scroll = max(0, min(self.scroll, max(0, len(self.agents) - VISIBLE_CARDS)))

        visible = self.agents[self.scroll : self.scroll + VISIBLE_CARDS]
        body_h = self.console.height - 3
        card_h = max(8, int(body_h * 29 / 88) - 2)
        grid = Table.grid(expand=True, padding=(0, 1, 0, 0))
        cells = [_card(a, self.scroll + i == self.selected, card_h) for i, a in enumerate(visible)]
        while len(cells) < VISIBLE_CARDS:
            cells.append(Panel(Text("", style=DIM), border_style=WHITE, padding=(0, 1), height=card_h))
        for _ in cells:
            grid.add_column(ratio=1)
        grid.add_row(*cells)

        return Panel(
            grid,
            border_style=f"bold {CYAN}" if self._focused("cards") else WHITE,
            padding=(0, 0),
        )

    def _detail_panel(self) -> Panel:
        if self.exploits_view:
            return self._exploits_panel()
        if not self.agents:
            return Panel(
                Text("  select an agent ←→", style=GRAY),
                border_style=f"bold {CYAN}" if self._focused("detail") else WHITE,
                padding=(0, 0),
            )
        a = self.agents[self.selected]
        pc = PHASE_COLOR.get(a.phase, WHITE)

        # boot stream: each agent's message lands one per second (~3s total)
        elapsed = time.time() - self._boot_t0
        if self._boot_t0 and elapsed < len(self.agents) + 1.0:
            stream = Text("\n")
            for i, ag in enumerate(self.agents):
                if elapsed >= i + 1:
                    stream.append(f"  agent {_hex_id(ag.pid)}  ", style=DIM)
                    stream.append(f"{ag.last_thought or ag.last_reasoning}\n\n", style=BLUE)
            return Panel(
                stream,
                title=f" [{CYAN}]Details[/{CYAN}] " if self._focused("detail") else f" [{DIM}]Details[/{DIM}] ",
                border_style=f"bold {CYAN}" if self._focused("detail") else WHITE,
                padding=(0, 0),
            )

        iters = list(getattr(a, "iterations", None) or [])
        it = iters[len(iters) - 1 - self.iter_pos] if iters else None

        lines = Text()
        lines.append(f"\n  agent {_hex_id(a.pid)}", style=f"bold {WHITE}")
        lines.append(f"  {a.target}", style=GRAY)
        lines.append("  ", style="")
        lines.append(_phase_label(a.phase), style=f"bold {pc}")
        if len(iters) > 1:
            shown = (it or {}).get("iteration", len(iters) - self.iter_pos)
            newest = iters[-1].get("iteration", len(iters))
            lines.append(f"  \u2039 iteration {shown}/{newest} \u203a", style=f"bold {CYAN}")
        else:
            lines.append(f"  iteration {a.iteration}", style=f"bold {CYAN}")
        ph = _phase_label(((it or {}).get("phase")) or a.phase)
        lines.append(f"  \u00b7  {ph}", style=f"bold {pc}")
        lines.append("\n")

        thought = ((it or {}).get("thought")) or a.last_thought
        if thought:
            lines.append("  | ", style=f"bold {CYAN}")
            lines.append("thought: ", style=f"dim {CYAN}")
            lines.append(thought, style=WHITE)
            lines.append("\n")

        paged_action = ((it or {}).get("action") or {}) if it else {}
        json_box = None
        tool = (paged_action.get("tool") if paged_action else None) or a.last_tool
        args = paged_action.get("args") if paged_action else None
        if args is None:
            args = a.last_args or {}
        if tool:
            # arg values capped for display — a python heredoc in `cmd`
            # is not a spec; the full payload lives in the audit trail
            shown_args = {k: (v if len(str(v)) <= 64 else str(v)[:61] + "…") for k, v in (args or {}).items()}
            call = {"tool": tool, "args": shown_args}
            try:
                call_json = json.dumps(call, ensure_ascii=False)
            except Exception:
                call_json = str(call)
            json_box = Panel(
                Syntax(call_json, "json", theme="monokai", word_wrap=True, background_color="default")
                if self.console.is_terminal
                else Text(call_json, style="white"),
                box=SQUARE,
                border_style="bright_white",
                title=" json ",
                title_align="left",
                padding=(0, 1),
            )

        rest = Text("\n")

        if a.cost_usd:
            rest.append("  | ", style=DIM)
            rest.append("cost ", style=DIM)
            rest.append(f"${a.cost_usd:.4f}", style=_cost_color(a.cost_usd))
            rest.append("\n")

        finds = a.findings or []
        if finds:
            rest.append(f"\n  -- findings ({len(finds)}) --\n", style=f"bold {GOLD}")
            for f in finds[:5]:
                if isinstance(f, dict):
                    sev = str(f.get("severity", "info")).lower()
                    rest.append("  | ", style=f"bold {SEV_COLOR.get(sev, DIM)}")
                    rest.append(f.get("endpoint", "?"), style=f"bold {WHITE}")
                    desc = str(f.get("description") or f.get("title") or "")
                    rest.append(f"  {desc}\n", style=WHITE)
                else:  # live digests publish titles only
                    rest.append("  | ", style=f"bold {SEV_COLOR['info']}")
                    rest.append(f"{str(f)}\n", style=WHITE)

        body = Group(lines, json_box, rest) if json_box is not None else Group(lines, rest)
        return Panel(
            body,
            title=f" [{CYAN}]Details[/{CYAN}] " if self._focused("detail") else f" [{DIM}]Details[/{DIM}] ",
            border_style=f"bold {CYAN}" if self._focused("detail") else WHITE,
            padding=(0, 0),
        )

    def _exploits_panel(self) -> Panel:
        """Details pane override (/exploits): every machine-confirmed
        exploit across all agents, severity-colored."""
        lines = Text("\n")
        lines.append("  confirmed exploits", style=f"bold {GOLD}")
        total = 0
        for a in self.agents:
            for f in a.findings or []:
                total += 1
                sev = str(f.get("severity", "info")).lower()
                lines.append("\n  | ", style=f"bold {SEV_COLOR.get(sev, DIM)}")
                lines.append(f"{f.get('endpoint', '?')}", style=f"bold {WHITE}")
                lines.append(f"  {_phase_label(a.phase)}", style=PHASE_COLOR.get(a.phase, WHITE))
                lines.append(f"\n      {f.get('description', '')}", style=GRAY)
        if not total:
            lines.append("\n  none confirmed yet", style=DIM)
        else:
            lines.append(f"\n\n  {total} confirmed", style=f"bold {WHITE}")
        lines.append("\n\n  select an agent to leave this view", style=DIM)
        return Panel(
            lines,
            title=f" [{CYAN}]Details[/{CYAN}] " if self._focused("detail") else f" [{DIM}]Details[/{DIM}] ",
            border_style=f"bold {CYAN}" if self._focused("detail") else WHITE,
            padding=(0, 0),
        )

    def _overview_panel(self) -> Panel:
        """Right column top — one dense stats strip: every band one row,
        no paragraph spacing, phases read as 'N in <phase>'."""
        n = len(self.agents)
        cost = sum(a.cost_usd or 0 for a in self.agents)
        iters = sum(a.iteration for a in self.agents)
        all_finds = self._all_dict_findings()
        sc = _sev_counts(all_finds)
        total = sum(sc.values())
        uptime = max((a.uptime for a in self.agents), default=0)
        phases: dict[str, int] = {}
        for a in self.agents:
            ph = _phase_label(a.phase)
            phases[ph] = phases.get(ph, 0) + 1

        strip = Text("\n ")
        strip.append(f"{n}", style=f"bold {WHITE}")
        strip.append(" agents  ", style=GRAY)
        strip.append(f"${cost:.2f}", style=_cost_color(cost))
        strip.append("  ", style=GRAY)
        strip.append(f"{iters}", style=f"bold {WHITE}")
        strip.append(" iters\n ", style=GRAY)

        strip.append(f"{sc['low']} LOW", style=f"bold {C_LOW}")
        strip.append("  ", style=GRAY)
        strip.append(f"{sc['medium']} MED", style=f"bold {C_MED}")
        strip.append("  ", style=GRAY)
        strip.append(f"{sc['high']} HIGH", style=f"bold {C_HIGH}")
        strip.append("  ", style=GRAY)
        strip.append(f"{sc['critical']} CRIT", style=f"bold {C_CRIT}")
        strip.append(f"   {total} findings\n ", style=GRAY)
        strip.append(_fmt_dur(uptime), style=f"italic {WHITE}")
        strip.append(" longest\n ", style=GRAY)

        for i, (ph, cnt) in enumerate(sorted(phases.items(), key=lambda x: -x[1])):
            if i:
                strip.append("  ", style=GRAY)
            strip.append(f"{cnt} in {ph}", style=f"bold {PHASE_COLOR.get(ph, WHITE)}")
        strip.append("\n")

        return Panel(
            Group(strip, _mascots(), _quote(), self._system_one_block()),
            title=f" [{CYAN}]Overview[/{CYAN}] " if self._focused("overview") else f" [{DIM}]Overview[/{DIM}] ",
            border_style=f"bold {CYAN}" if self._focused("overview") else WHITE,
            padding=(0, 0),
        )

    def _system_one_block(self):
        """System One decision models — a white rule, then engine,
        model status and live counters aggregated across sessions."""
        return Group(Rule(style=WHITE), self._system_one_text())

    def _system_one_text(self) -> Text:
        blocks = [a.system_one for a in self.agents if getattr(a, "system_one", None)]
        t = Text("\n System One ", style=GRAY)
        if not blocks:
            t.append("not running", style=DIM)
            return t
        eng = str(blocks[0].get("engine") or "?")
        status = str(blocks[0].get("status") or "?")
        dec = sum(int(b.get("decisions") or 0) for b in blocks)
        fb = sum(int(b.get("fallbacks") or 0) for b in blocks)
        with_vals = [float(b.get("ms_avg") or 0) for b in blocks]
        b_avg = round(sum(with_vals) / len(with_vals), 1) if with_vals else 0.0
        t.append(eng, style=f"bold {WHITE}")
        t.append("  ·  ", style=DIM)
        t.append(status, style=f"bold {GREEN}" if status == "loaded" else f"bold {C_MED}")
        t.append(f"\n {dec}", style=f"bold {WHITE}")
        t.append(" decisions  ·  ", style=GRAY)
        t.append(f"{fb}", style=f"bold {RED}" if fb else f"bold {WHITE}")
        t.append(" fallback  ·  ", style=GRAY)
        t.append(f"{b_avg}ms", style=GRAY)
        return t

    def _mesh_panel(self) -> Panel:
        """Right column bottom — mesh chat feed (design.png: 36% height).
        Chat order: oldest at top, NEWEST AT THE BOTTOM — and bottom-
        anchored, so the freshest lines always fit (the oldest drop off
        the top when the panel is short)."""
        lines = Text()
        if self.chat:
            width = max(20, int(self.console.width * 0.32) - 6)
            budget = max(1, int((self.console.height - 3) * 36 / 88) - 2)
            kept: list[str] = []
            used = 0
            for msg in reversed(self.chat):
                rows = max(1, -(-len(msg) // width))
                if used + rows > budget and kept:
                    break
                kept.append(msg)
                used += rows
            for msg in reversed(kept):
                lines.append_text(self._chat_line(msg))
                lines.append("\n")
        else:
            lines.append("\n  (no mesh activity)\n", style=DIM)
        return Panel(
            lines,
            title=f" [{CYAN}]Mesh[/{CYAN}] " if self._focused("mesh") else f" [{DIM}]Mesh[/{DIM}] ",
            border_style=f"bold {CYAN}" if self._focused("mesh") else WHITE,
            padding=(0, 0),
        )

    def _chat_line(self, msg: str) -> Text:
        t = Text()
        t.append("  ", style="")
        t.append(msg.split(" [")[0], style=f"dim {CYAN}")
        rest = msg[len(msg.split(" [")[0]) :]
        if rest.startswith(" ["):
            end = rest.find("]")
            if end != -1:
                tag = rest[: end + 1]
                # [agent:37771] -> [agent:00938b] — match the cards' hex ids
                inner = tag.strip()[1:-1]
                if ":" in inner:
                    who, pid = inner.split(":", 1)
                    if pid.isdigit():
                        tag = f" [{who.strip()}:{int(pid):06x}]"
                t.append(tag, style=f"dim {MAGENTA}")
                rest = rest[end + 1 :]
        t.append(f"{rest}", style=GRAY)
        return t

    def _input_area(self):
        """Bottom-left — the suijin-style input box with command prediction
        floating above it (design.png: 15% of height)."""
        rows = []
        if self.input_buf.startswith("/") and self._filtered_suggestions():
            for i, (name, desc) in enumerate(self._filtered_suggestions()[:6]):
                if i == self.sug_sel:
                    rows.append(
                        Text.assemble(("  › ", f"bold {CYAN}"), (f"{name:<16}", f"bold {CYAN}"), (desc[:40], CYAN))
                    )
                else:
                    rows.append(Text.assemble(("    ", DIM), (f"{name:<16}", ""), (desc[:40], DIM)))

        prompt = Text()
        who = "root" if self.su_pid is None else _hex_id(self.su_pid)
        prompt.append(f" {who} » ", style=f"bold {GOLD}")
        prompt.append(self.input_buf, style=f"bold {WHITE}")
        prompt.append("█", style=f"bold {CYAN}")

        box = Panel(prompt, border_style=f"bold {CYAN}" if self._focused("input") else WHITE, padding=(0, 0))
        # the hint rides a FIXED column beside the box — typing grows the
        # buffer, never pushes the hint off (the disappearing-hint bug)
        hints = Text(
            "\n tab focus · j/k page cards\n ←→ history · / cmds",
            style=DIM,
        )
        g = Table.grid(expand=True, pad_edge=False)
        g.add_column(ratio=1)
        g.add_column(width=26)
        g.add_row(box, hints)
        lead = list(rows)
        if self.note and time.time() - self.note_at < 5.0:
            lead.append(Text(f"  {self.note}", style=GRAY))
        if lead:
            return Group(*lead, g)
        return Group(Text(""), g)

    # ── layout ───────────────────────────────────────────────────────

    def _build(self) -> Layout:
        """design.png proportions: strip / body(62% left: cards 29 + detail
        44 + input 15 | 32% right: overview 52 + mesh 36)."""
        layout = Layout(name="root")
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
        )
        layout["body"].split_row(
            Layout(name="left", ratio=62),
            Layout(name="right", ratio=32),
        )
        layout["left"].split_column(
            Layout(name="cards", ratio=29),
            Layout(name="detail", ratio=44),
            Layout(name="input", ratio=15),
        )
        layout["right"].split_column(
            Layout(name="overview", ratio=52),
            Layout(name="mesh", ratio=36),
        )
        return layout

    def _render(self, layout: Layout) -> None:
        with self._lock:
            layout["header"].update(self._header())
            layout["cards"].update(self._agents_panel())
            layout["detail"].update(self._detail_panel())
            layout["input"].update(self._input_area())
            layout["overview"].update(self._overview_panel())
            layout["mesh"].update(self._mesh_panel())

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
            self._boot_t0 = time.time()
            layout = self._build()
            with Live(layout, console=self.console, refresh_per_second=20, screen=True):
                while self.running:
                    self._render(layout)
                    r, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if r:
                        ch = sys.stdin.read(1)
                        if ch == "\x1b":  # arrow/esc sequence: grab the tail
                            for _ in range(2):
                                r2, _, _ = select.select([sys.stdin], [], [], 0.05)
                                if not r2:
                                    break
                                ch += sys.stdin.read(1)
                        self._key(ch)
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
            self.running = False

    def _render_once(self) -> None:
        layout = self._build()
        self._render(layout)
        shot = Console(
            width=self.console.width,
            height=40,
            force_terminal=True,
            highlight=False,
        )
        shot.print(layout)

    # ── command input (suijin-style prediction) ─────────────────────

    def _filtered_suggestions(self) -> list[tuple[str, str]]:
        buf = self.input_buf
        if not buf.startswith("/"):
            return []
        q = buf[1:].split(" ")[0].lower()
        items = sorted((AGENT_COMMANDS if self.su_pid else COMMANDS).items())
        if not q:
            return items
        prefix = [(k, v) for k, v in items if k.lstrip("/").startswith(q)]
        substr = [(k, v) for k, v in items if q in k.lstrip("/") and (k, v) not in prefix]
        return prefix + substr

    def _su_agent(self):
        """The Agent we're su'd into, or None (dropped agents release)."""
        if self.su_pid is None:
            return None
        for a in self.agents:
            if a.pid == self.su_pid:
                return a
        self.su_pid = None
        return None

    def _guidance_file(self, a) -> Path:
        return Path(getattr(a, "engagement_dir", None) or "") / "state" / "live_guidance.md"

    def _write_agent_guidance(self, a, tag: str, text: str) -> bool:
        gp = self._guidance_file(a)
        if not getattr(a, "engagement_dir", None):
            return False
        try:
            gp.parent.mkdir(parents=True, exist_ok=True)
            with gp.open("a", encoding="utf-8") as fh:
                fh.write(f"- {time.strftime('%H:%M:%S')} [{tag}] OPERATOR: {text[:400]}\n")
            return True
        except OSError:
            return False

    def _execute_agent(self, cmd: str, args: str, argv: list[str]) -> None:
        """Engagement commands while su'd into an agent — the same file
        surfaces the in-window pause console uses (guidance, notes,
        audit). Free text (no slash) is operator guidance to the agent."""
        a = self._su_agent()
        if a is None:
            self._note("su target gone — back to root")
            return
        if not cmd.startswith("/"):
            ok = self._write_agent_guidance(a, "OPERATOR", cmd + ((" " + args) if args else ""))
            self._note("guidance queued" if ok else "no engagement dir for this agent")
        elif cmd == "/state":
            self._note(
                f"{_hex_id(a.pid)} {_phase_label(a.phase)} iter {a.iteration} · "
                f"{len(a.findings or [])} confirmed · ${a.cost_usd or 0:.3f} · up {_fmt_dur(a.uptime)}"
            )
        elif cmd == "/audit":
            n_iters = len(getattr(a, "iterations", None) or [])
            self._note(
                f"audit: {n_iters} recorded iterations · {len(a.findings or [])} confirmed · "
                f"{a.ok_actions or 0}/{a.total_actions or 0} ok · ${a.cost_usd or 0:.3f}"
            )
        elif cmd == "/cost":
            self._note(f"${a.cost_usd or 0:.4f} · {a.total_actions or 0} actions · {a.iteration} iterations")
        elif cmd == "/findings":
            finds = [f.get("endpoint", "?") for f in (a.findings or [])]
            self._note("confirmed: " + (" ".join(finds) if finds else "none"))
        elif cmd == "/note":
            if not args:
                self._note("usage: /note <text>")
                return
            eng = Path(getattr(a, "engagement_dir", "") or "")
            notes = eng / ".notes" / "engagement_notes.md"
            try:
                notes.parent.mkdir(parents=True, exist_ok=True)
                with notes.open("a", encoding="utf-8") as fh:
                    fh.write(f"- {time.strftime('%Y-%m-%d %H:%M:%S')} OPERATOR (console): {args}\n")
                self._note("note written")
            except OSError:
                self._note("note write failed")
        elif cmd == "/objective":
            if not args:
                self._note("usage: /objective <new objective>")
            elif self._write_agent_guidance(a, "OBJECTIVE", args):
                self._note("objective change queued — the agent reads it next turn")
            else:
                self._note("no engagement dir for this agent")
        elif cmd == "/panic":
            try:
                self.mesh_dir.mkdir(parents=True, exist_ok=True)
                (self.mesh_dir / "pause-operator").write_text("panic by operator\n")
                self._write_agent_guidance(a, "PANIC", "hold position; await operator")
                self._note("agent held (pause flag) — /resume releases")
            except OSError:
                self._note("panic failed")
        else:
            self._note(f"unknown (agent ctx): {cmd}")

    def _execute(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        self.history.append(line)
        self.hist_idx = None
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""
        argv = args.split()

        if cmd == "/su":
            target = argv[0] if argv else ""
            if target.lower() == "root":
                self.su_pid = None
                self._note("root operator")
            elif not target:
                self._note("usage: /su <hex> | /su root")
            else:
                for a in self.agents:
                    if _hex_id(a.pid).startswith(target.lower()):
                        self.su_pid = a.pid
                        self.exploits_view = False
                        self.focus = self.AREAS.index("input")
                        self._note(f"su {_hex_id(a.pid)} — engagement commands active")
                        return
                self._note(f"no agent matches {target}")
            return

        if self.su_pid is not None:
            self._execute_agent(cmd, args, argv)
            return

        if cmd in ("/quit", "/q"):
            self.running = False
        elif cmd == "/next" or cmd == "n":
            self._select_agent(self.selected + 1)
        elif cmd == "/prev":
            self._select_agent(self.selected - 1)
        elif cmd == "/agent":
            if not argv:
                self._note("usage: /agent <hex>")
            elif not self._select_by_hex(argv[0]):
                self._note(f"no agent matches {argv[0]}")
            else:
                self._note(f"agent {argv[0]}")
        elif cmd == "/focus":
            want = (argv[0].lower() if argv else "").replace("details", "detail")
            if want in self.AREAS:
                self.focus = self.AREAS.index(want)
                self._note(f"focus: {want}")
            else:
                self._note("usage: /focus cards|details|overview|mesh|input")
        elif cmd == "/say":
            if not args:
                self._note("usage: /say <message>")
            else:
                self._say(args)
        elif cmd == "/exploits":
            self.exploits_view = True
            self.focus = self.AREAS.index("detail")
            self._note("confirmed exploits listed in Details")
        elif cmd == "/tail":
            if not argv:
                self._note("usage: /tail <hex>")
            elif not self._select_by_hex(argv[0]):
                self._note(f"no agent matches {argv[0]}")
            else:
                self.exploits_view = False
                self.focus = self.AREAS.index("detail")
                self._note(
                    f"tailing {self.agents[self.selected].hex if hasattr(self.agents[self.selected], 'hex') else _hex_id(self.agents[self.selected].pid)}"
                )
        elif cmd == "/sort":
            if argv and argv[0] in ("iter", "cost", "finds", "uptime"):
                self.sort_key = argv[0]
                self._poll_once()
                self._note(f"sorted by {argv[0]}")
            else:
                self._note("usage: /sort iter|cost|finds|uptime")
        elif cmd == "/filter":
            if not argv or argv[0] == "all":
                self.filter_phase = None
                self._poll_once()
                self._note("filter cleared")
            elif argv[0] in ("recon", "informational", "exploitation", "post_exploitation"):
                self.filter_phase = argv[0]
                self._poll_once()
                self._note(f"filtered: {argv[0]}")
            else:
                self._note("usage: /filter recon|exploitation|post_exploitation|all")
        elif cmd == "/pause":
            try:
                self.mesh_dir.mkdir(parents=True, exist_ok=True)
                (self.mesh_dir / "pause-operator").write_text("paused by operator\n")
                if args:
                    queued = 0
                    for a in self.agents:
                        eng = getattr(a, "engagement_dir", None)
                        if not eng:
                            continue
                        gp = Path(eng) / "state" / "live_guidance.md"
                        gp.parent.mkdir(parents=True, exist_ok=True)
                        with gp.open("a", encoding="utf-8") as fh:
                            fh.write(f"- {time.strftime('%H:%M:%S')} [PAUSED] OPERATOR: {args[:400]}\n")
                        queued += 1
                    self._note(f"paused all agents · message queued to {queued}")
                else:
                    self._note("paused all agents — /resume to release")
            except OSError as e:
                self._note(f"pause failed: {e}")
        elif cmd == "/resume":
            with contextlib.suppress(OSError):
                (self.mesh_dir / "pause-operator").unlink(missing_ok=True)
            self._note("agents resuming")
        elif cmd == "/clear":
            p = self.mesh_dir / "gc.log"
            try:
                self._chat_offset = p.stat().st_size
            except OSError:
                self._chat_offset = 0
            self.chat = []
            self._note("chat cleared")
        elif cmd == "/refresh":
            self._poll_once()
            self._note("rescanned")
        else:
            self._note(f"unknown: {cmd}")

    def _select_by_hex(self, prefix: str) -> bool:
        for i, a in enumerate(self.agents):
            if _hex_id(a.pid).startswith(prefix.lower()):
                self._select_agent(i)
                return True
        return False

    def _say(self, msg: str) -> None:
        """The operator speaks to the mesh — appended to gc.log, agents
        read it when they build their next context."""
        try:
            self.mesh_dir.mkdir(parents=True, exist_ok=True)
            gc = self.mesh_dir / "gc.log"
            pre_size = gc.stat().st_size if gc.exists() else 0
            stamp = time.strftime("%m-%d %H:%M:%S")
            with gc.open("a", encoding="utf-8") as fh:
                fh.write(f"{stamp}|{os.getpid()}|operator|{msg[:400]}\n")
            self._chat_offset = pre_size  # our line (and later) surface
            self._poll_once()
            self._note("broadcast to mesh")
        except OSError as e:
            self._note(f"mesh unavailable: {e}")

    def _select_agent(self, idx: int) -> None:
        with self._lock:
            self.selected = max(0, min(idx, max(0, len(self.agents) - 1)))
            self.iter_pos = 0
            self.exploits_view = False

    def _page_cards(self, delta: int) -> None:
        """Cards focus: j/k page a whole window (5 agents) at a time —
        no individual focusing; the selection rides the window anchor."""
        n = len(self.agents)
        if not n:
            return
        max_scroll = max(0, n - VISIBLE_CARDS)
        self.scroll = max(0, min(max_scroll, self.scroll + delta * VISIBLE_CARDS))
        self._select_agent(self.scroll)

    def _page_iter(self, delta: int) -> None:
        """Detail history pager: +1 older, -1 newer."""
        if not self.agents:
            return
        a = self.agents[self.selected]
        n = len(getattr(a, "iterations", None) or [])
        self.iter_pos = max(0, min(max(0, n - 1), self.iter_pos + delta))

    def _key(self, ch: str) -> None:
        # tab ALWAYS cycles focus — the input box never takes it
        if ch == "\t":
            self.focus = (self.focus + 1) % len(self.AREAS)
            return

        if self._focused("input"):
            if ch in ("\r", "\n"):
                line, self.input_buf = self.input_buf, ""
                self.sug_sel = 0
                self._execute(line)
            elif ch in ("\x7f", "\x08"):
                self.input_buf = self.input_buf[:-1]
                self.sug_sel = 0
            elif ch == "\x1b":  # bare Esc clears
                self.input_buf = ""
                self.sug_sel = 0
            elif ch in ("\x1b[A", "\x1b[B"):
                # priority: active history recall > prediction pick > ring
                if self.hist_idx is not None or (not self.input_buf and self.history):
                    if self.hist_idx is None:
                        self.hist_idx = len(self.history) - (1 if ch == "\x1b[A" else 0)
                    else:
                        self.hist_idx = max(
                            0, min(len(self.history) - 1, self.hist_idx + (-1 if ch == "\x1b[A" else 1))
                        )
                    self.input_buf = self.history[self.hist_idx]
                    self.sug_sel = 0
                elif self.input_buf.startswith("/") and self._filtered_suggestions():
                    matches = self._filtered_suggestions()
                    delta = 1 if ch == "\x1b[B" else -1
                    self.sug_sel = (self.sug_sel + delta) % len(matches)
                else:  # nothing to pick — keep the focus ring moving
                    delta = -1 if ch == "\x1b[A" else 1
                    self.focus = (self.focus + delta) % len(self.AREAS)
            elif ch in ("\x1b[C", "\x1b[D"):
                pass  # ←→ ignored in the box
            elif ch >= " " and len(ch) == 1:
                self.input_buf += ch
                self.sug_sel = 0
            return

        # NAV — every non-input panel
        if ch == "q":
            self.running = False
        elif ch == "\x1b[A":  # ↑ previous focus
            self.focus = (self.focus - 1) % len(self.AREAS)
        elif ch == "\x1b[B":  # ↓ next focus
            self.focus = (self.focus + 1) % len(self.AREAS)
        elif ch in ("\x1b[C", "k"):  # → / k = right
            if self._focused("cards"):
                self._page_cards(1)
            elif self._focused("detail") and ch == "\x1b[C":
                self._page_iter(-1)  # newer
            else:
                self._select_agent(self.selected + 1)
        elif ch in ("\x1b[D", "j"):  # ← / j = left
            if self._focused("cards"):
                self._page_cards(-1)
            elif self._focused("detail") and ch == "\x1b[D":
                self._page_iter(1)  # older
            else:
                self._select_agent(self.selected - 1)


# ═══════════════════════════════════════════════════════════════════════
# Entry
# ═══════════════════════════════════════════════════════════════════════


def main(demo: bool = False) -> int:
    if demo:
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="suijin_operator_demo_"))
        mesh, ws = tmp / "mesh", tmp / "workspace"
        write_demo(mesh, ws)
        tui = OperatorTUI(mesh, ws, live=False)
        print(f"  demo data: {tmp}")
        print("  press q to quit\n")
        tui.run()
        return 0

    tui = OperatorTUI(MESH_DIR, WORKSPACE_DIR)
    tui.run()
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(prog="operator", description="Suijin Console — multi-agent command center")
    ap.add_argument("--demo", action="store_true", help="render with mock data (no live agents needed)")
    args = ap.parse_args()
    raise SystemExit(main(demo=args.demo))
