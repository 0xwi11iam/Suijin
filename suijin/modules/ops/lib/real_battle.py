"""REAL battle — the LLM red agent vs the live blue detector on one lab.

Not scripted: `suijin battle --real` boots the vulnerable lab, drives the
ACTUAL agent graph against it (findings come from the agent's own
tool calls), then scores every request the lab saw with the REAL
anomaly detector. Labels are ground truth: a traffic entry is an
"attack" iff the red agent itself issued it — blue's catch rate on
those is the honest adversarial metric.

`--mock` (CI default): same pipeline end-to-end — real lab, real
detector, real dispatch — with a scripted LLM whose responses are real
tool calls against known lab endpoints. Deterministic, offline.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

FLAG_RE = re.compile(r"FLAG\{[^}]+\}")
DEFAULT_PORT = 5907


def _boot_lab(port: int):
    log = Path("/tmp/blue_defend_traffic.jsonl")
    log.write_text("")  # fresh traffic for this battle
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve().parents[3] / "lab" / "blue_target" / "vulnerable_app.py")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={"PORT": str(port), "PATH": "/usr/bin:/bin:/usr/local/bin:" + __import__("os").environ.get("PATH", "")},
    )
    import requests

    for _ in range(40):
        try:
            requests.get(f"http://127.0.0.1:{port}/", timeout=1)
            return proc
        except Exception:
            time.sleep(0.25)
    proc.kill()
    raise RuntimeError("lab failed to boot")


class RedRecorder:
    """route_tool wrapper: records the requests the RED agent issues —
    the ground-truth attack labels for blue's catch rate."""

    def __init__(self, inner, port: int):
        self.inner = inner
        self.port = port
        self.requests: list[tuple[str, str]] = []  # (method, path)
        self.flags: set[str] = set()
        self.calls = 0

    def __call__(self, tool_name, args, config):
        self.calls += 1
        try:
            out = str(self.inner(tool_name, args, config))
        except Exception as e:  # noqa: BLE001 — tool failures are data
            out = f"Error: {e}"
        if tool_name == "http_request":
            url = str(args.get("url", ""))
            path = url.split(f":{self.port}", 1)[-1].split("?")[0] or "/"
            path = "/" + path.lstrip("/") if not path.startswith("/") else path
            self.requests.append((str(args.get("method", "GET")).upper(), path))
        if tool_name == "execute_terminal":
            for m in re.finditer(rf":{self.port}(\S+)", str(args.get("cmd", ""))):
                self.requests.append(("GET", m.group(1).split("?")[0]))
        self.flags.update(FLAG_RE.findall(out))
        return out


def _mock_generate(port: int):
    """Scripted LLM: every response is a REAL tool call into the lab.
    Exercises the full decision->dispatch->lab->observe pipeline offline."""
    script = [
        {
            "action": "use_tool",
            "tool_name": "http_request",
            "tool_args": {"method": "GET", "url": f"http://127.0.0.1:{port}/api/login"},
            "thought": "probe login",
        },
        {
            "action": "use_tool",
            "tool_name": "http_request",
            "tool_args": {
                "method": "POST",
                "url": f"http://127.0.0.1:{port}/api/login",
                "body": "username=admin'--&password=x",
            },
            "thought": "auth bypass SQLi",
        },
        {
            "action": "use_tool",
            "tool_name": "http_request",
            "tool_args": {"method": "GET", "url": f"http://127.0.0.1:{port}/api/profile?user=2"},
            "thought": "IDOR check",
        },
        {
            "action": "use_tool",
            "tool_name": "http_request",
            "tool_args": {"method": "GET", "url": f"http://127.0.0.1:{port}/api/documents?path=../../etc/hostname"},
            "thought": "path traversal",
        },
        {
            "action": "use_tool",
            "tool_name": "http_request",
            "tool_args": {"method": "GET", "url": f"http://127.0.0.1:{port}/api/search?q=<script>alert(1)</script>"},
            "thought": "reflected XSS probe",
        },
        {
            "action": "use_tool",
            "tool_name": "http_request",
            "tool_args": {"method": "GET", "url": f"http://127.0.0.1:{port}/admin", "headers": {"X-Admin": "true"}},
            "thought": "admin panel via header bypass + flag",
        },
        {"action": "complete", "completion_reason": "probes complete", "thought": "done"},
    ]
    import asyncio

    async def gen(messages, config=None, **kw):
        await asyncio.sleep(0.02)
        return json.dumps(script.pop(0) if script else script[-1])

    return gen


def run_real_battle(port: int | None = None, mock: bool = False, objective: str = "") -> dict:
    """Full adversarial cycle. Returns the verdict dict."""

    port = port or DEFAULT_PORT
    # kill anything on the port first (best effort)
    try:
        pid = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=3).stdout.strip()
        if pid:
            subprocess.run(["kill", pid.strip()], timeout=3)
    except Exception:  # noqa: BLE001
        pass

    lab = _boot_lab(port)
    try:
        from suijin.modules.providers.lib import reset_usage
        from suijin.modules.tools.lib.dispatch import route_tool

        recorder = RedRecorder(route_tool, port)
        reset_usage()

        objective = objective or (
            f"Attack the vulnerable lab at http://127.0.0.1:{port} — probe endpoints, exploit what you find "
            "(SQLi, IDOR, traversal, XSS, auth flaws), and capture every FLAG{{...}} you can. Work efficiently."
        )

        from suijin.modules.agent.lib.agent_graph import SuijinAgentGraph

        if mock:
            generate = _mock_generate(port)
        else:
            from suijin.modules.redteam.lib.red.llm_client import generate_async as generate

        graph = SuijinAgentGraph(
            generate_fn=generate, route_tool_fn=recorder, max_iterations=12, run_config={"max_cost_usd": 5.0}
        )
        import asyncio

        t0 = time.monotonic()
        final_state = asyncio.run(graph.run(objective, thread_id=f"battle_{int(time.monotonic())}"))
        duration = time.monotonic() - t0

        red = {
            "iterations": final_state.get("current_iteration", 0),
            "tool_calls": recorder.calls,
            "requests_issued": len(recorder.requests),
            "flags": sorted(recorder.flags),
            "completion": final_state.get("completion_reason", "?"),
        }

        # ── BLUE: score the traffic the lab actually saw ──────────────
        from suijin.modules.blueteam.lib.blue.traffic.anomaly_detector import detect_anomalies

        entries = []
        log = Path("/tmp/blue_defend_traffic.jsonl")
        if log.exists():
            for line in log.read_text().splitlines():
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
        attack_paths = {(m, p) for m, p in recorder.requests}
        attacks = [e for e in entries if (str(e.get("method", "GET")).upper(), str(e.get("path", "/"))) in attack_paths]
        caught = 0
        missed_examples = []
        for e in attacks:
            signals = detect_anomalies(e, {"methods": {e.get("method", "GET"): 1}, "ips": set(), "avg_body_size": 100})
            if sum(s[1] for s in signals) >= 4:  # one confirmed attack-class signal (sql/xss = weight 4)
                caught += 1
            else:
                missed_examples.append(f"{e.get('method')} {str(e.get('path'))[:40]}")
        blue = {
            "traffic_seen": len(entries),
            "attack_requests": len(attacks),
            "caught": caught,
            "missed": len(attacks) - caught,
            "recall": round(caught / len(attacks), 3) if attacks else 0.0,
            "miss_examples": missed_examples[:5],
        }
        verdict = {
            "port": port,
            "mode": "mock" if mock else "live-llm",
            "duration_s": round(duration, 1),
            "red": red,
            "blue": blue,
            "winner": "red" if red["flags"] and blue["recall"] < 0.6 else ("blue" if blue["recall"] >= 0.6 else "draw"),
        }
        _write_report(verdict)
        return verdict
    finally:
        lab.kill()


def render_real_verdict(v: dict) -> str:
    r, b = v["red"], v["blue"]
    lines = [
        f"REAL BATTLE ({v['mode']}, {v['duration_s']}s on :{v['port']}) — winner: {v['winner'].upper()}",
        f"red : {r['iterations']} iterations, {r['tool_calls']} tool calls, flags: {', '.join(r['flags']) or 'NONE'}",
        f"blue: saw {b['traffic_seen']} requests; of red's {b['attack_requests']} attacks caught {b['caught']} "
        f"(recall {b['recall']:.0%})",
    ]
    if b["miss_examples"]:
        lines.append("  blue missed: " + "; ".join(b["miss_examples"]))
    return "\n".join(lines)


def _write_report(v: dict) -> None:
    try:
        from suijin.modules.platform.lib.workspace import artifact_dir

        d = artifact_dir("reports")
        d.mkdir(parents=True, exist_ok=True)
        (d / f"real_battle_{int(time.time())}.json").write_text(json.dumps(v, indent=2))
    except Exception:  # noqa: BLE001
        pass
