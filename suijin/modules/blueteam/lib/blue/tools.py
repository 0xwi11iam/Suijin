"""Blue tool registry — the arsenal the blue agent (and shell) drives.

NAMESPACED BY DESIGN (BF1): blue tools live in their own registry and
are rendered into BLUE prompts only. They are deliberately NOT
kernel-registered — red and blue run in one process during a
deathmatch, and kernel registration would leak blue tools into red's
fireteam prompts. Cross-visibility happens only where intended.

Every action lands on the enforcement plane (instant, no app mutation)
or runs through the gated shell (red's guardrail treatment).
"""

from __future__ import annotations

import subprocess


def _enf():
    from suijin.modules.blueteam.lib.blue import enforcement

    return enforcement


# ── the registry: name -> (fn, arg spec, description) ──────────────────


def _blue_tarpit(ip: str, seconds: float = 8.0, args: dict = None) -> str:
    from suijin.modules.blueteam.lib.blue.defense.tarpit import engage

    a = args or {}
    engage(str(ip), delay=min(float(a.get("seconds", seconds) or seconds), 15.0))
    return f"TARPIT {ip} — every request delayed up to {min(float(a.get('seconds', seconds) or seconds), 15.0):.0f}s"


def _blue_block(ip: str = "", reason: str = "", args: dict = None) -> str:
    a = args or {}
    target = str(a.get("ip", ip) or "")
    if not target:
        return "Error: ip required"
    return _enf().block_ip(target, str(a.get("reason", reason) or "blue agent decision"))


def _blue_unblock(ip: str = "", args: dict = None) -> str:
    a = args or {}
    target = str(a.get("ip", ip) or "")
    if not target:
        return "Error: ip required"
    return _enf().unblock_ip(target)


def _blue_blocks(args: dict = None) -> str:
    ips = _enf().blocked_ips()
    return "blocked IPs: " + (", ".join(ips) if ips else "(none)")


def _blue_honeypot(path: str = "", content: str = "", args: dict = None) -> str:
    a = args or {}
    p = str(a.get("path", path) or "")
    c = str(a.get("content", content) or '{"error": "not found"}')
    return _enf().serve_honeypot(p, c, str(a.get("content_type", "application/json")))


def _blue_fake(path: str = "", body: str = "", status: int = 200, args: dict = None) -> str:
    a = args or {}
    p = str(a.get("path", path) or "")
    b = str(a.get("body", body) or "maintenance")
    return _enf().fake_response(p, b, int(a.get("status", status) or 200))


def _blue_redirect(ip: str = "", url: str = "", args: dict = None) -> str:
    a = args or {}
    return _enf().redirect_ip(str(a.get("ip", ip) or "?"), str(a.get("url", url) or "https://example.invalid/honeypot"))


def _blue_canary(token: str = "", note: str = "", args: dict = None) -> str:
    a = args or {}
    return _enf().arm_canary(str(a.get("token", token) or ""), str(a.get("note", note) or ""))


def _blue_canary_hits(args: dict = None) -> str:
    hits = _enf().canary_hits()
    if not hits:
        return "no canary hits yet"
    lines = [f"{h['ts']} {h['ip']:16} {h['path'][:40]} ({h['note'][:40]})" for h in hits[-15:]]
    return f"{len(hits)} canary hit(s):\n  " + "\n  ".join(lines)


def _blue_force_rotate(reason: str = "", args: dict = None) -> str:
    """Hill-specific lever: invalidate whatever the attacker already stole."""
    from pathlib import Path

    from suijin.modules.platform.lib.constants import TMP_DIR

    a = args or {}
    why = str(a.get("reason", reason) or "blue agent")
    r = Path(TMP_DIR) / "hill_force_rotate"
    try:
        r.write_text(why)
        return (
            f"FORCE-ROTATE requested ({why}) — the hill lab mints a fresh token; previously captured tokens are invalid"
        )
    except OSError as e:
        return f"Error: {e}"


_DANGEROUS_PATTERNS = (
    "rm -rf /",
    "rm -rf ~",
    "mkfs.",
    "dd if=",
    ":(){ :|:& };:",
    "> /dev/sd",
    "shutdown",
    "reboot",
    "halt",
)


def _blue_shell(cmd: str = "", args: dict = None) -> str:
    """Gated shell — the blue agent's creative freedom with red's guardrails:
    dangerous patterns refused unless SUIJIN_AUTO_APPROVE=true."""
    import os

    a = args or {}
    command = str(a.get("cmd", cmd) or "")
    if not command.strip():
        return "Error: cmd required"
    low = command.lower().replace(" ", "")
    if any(p.lower().replace(" ", "") in low for p in _DANGEROUS_PATTERNS) and os.environ.get("SUIJIN_AUTO_APPROVE", "").lower() != "true":
        return f"REFUSED: command matches a dangerous pattern ({command[:60]}…). Set SUIJIN_AUTO_APPROVE=true to override."
    try:
        proc = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True, timeout=30)
        out = (proc.stdout or "") + (("\n[stderr] " + proc.stderr) if proc.stderr else "")
        return f"[exit {proc.returncode}] {out[:4000]}" if out.strip() else f"[exit {proc.returncode}] (no output)"
    except subprocess.TimeoutExpired:
        return "Error: command timed out (30s)"
    except Exception as e:  # noqa: BLE001 — shell errors are data
        return f"Error: {e}"


# name -> (fn, params, description) — the render shape mirrors pack manifests
BLUE_TOOLS: dict[str, tuple] = {
    "blue_tarpit": (_blue_tarpit, ["ip", "seconds"], "Delay every request from an IP (enforcement plane, instant)"),
    "blue_block": (_blue_block, ["ip", "reason"], "403 an IP at the proxy — the strongest lever"),
    "blue_unblock": (_blue_unblock, ["ip"], "Lift a proxy block"),
    "blue_blocks": (_blue_blocks, [], "List blocked IPs"),
    "blue_honeypot": (
        _blue_honeypot,
        ["path", "content"],
        "Serve crafted content at a path prefix instead of the real app",
    ),
    "blue_fake_response": (_blue_fake, ["path", "body", "status"], "Serve a plain fake response at a path prefix"),
    "blue_redirect": (_blue_redirect, ["ip", "url"], "302 a specific IP to a URL of your choosing"),
    "blue_arm_canary": (_blue_canary, ["token", "note"], "Arm a canary token — any request containing it trips"),
    "blue_canary_hits": (_blue_canary_hits, [], "Canary tripwire hits (time, IP, path)"),
    "blue_force_rotate": (_blue_force_rotate, ["reason"], "Hill lab: invalidate stolen tokens immediately"),
    "blue_shell": (_blue_shell, ["cmd"], "Run a shell command (gated: dangerous patterns need SUIJIN_AUTO_APPROVE)"),
}


def route_blue_tool(name: str, args: dict) -> str:
    """Dispatch one blue tool. Unknown names return guidance."""
    entry = BLUE_TOOLS.get(name)
    if entry is None:
        close = [n for n in BLUE_TOOLS if name and name.split("_")[-1] in n][:3]
        return f"Unknown blue tool {name!r}. Closest: {', '.join(close) or '(none)'}"
    fn, _params, _desc = entry
    try:
        return str(fn(args=args or {}))
    except Exception as e:  # noqa: BLE001 — tool failures are data
        return f"Error: {e}"


def render_blue_tools() -> str:
    """The blue tool catalog for prompts (rendered ONLY into blue prompts)."""
    lines = ["## BLUE TOOLS (your arsenal — instant effect at the proxy)"]
    for name, (_fn, params, desc) in sorted(BLUE_TOOLS.items()):
        p = ", ".join(params) if params else "no args"
        lines.append(f"- **{name}** ({p}) — {desc}")
    return "\n".join(lines)
