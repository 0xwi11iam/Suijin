"""Blue enforcement plane — defenses applied AT THE PROXY.

The BF1 model: the proxy is the front door. Defenses written here take
effect on the NEXT request — no app reload, no source mutation, no
poisoning the target. The existing tarpit file convention is one lever
in this plane; this module adds blocks, honeypot routes, fake
responses, redirects, and the canary tripwire, all in one JSON state
file the proxy reads per-request.

State file (workspace tmp by default, env BLUE_ENFORCEMENT_FILE):
{
  "blocks": {"<ip>": {"ts": ..., "reason": ...}},
  "honeypots": {"<path-prefix>": {"content": ..., "content_type": ..., "status": 200}},
  "fakes": {"<path-prefix>": {"body": ..., "status": 200}},
  "redirects": {"<ip>": "<url>"},
  "canaries": {"<token>": {"note": ..., "ts": ...}},
  "canary_hits": [...]
}
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from pathlib import Path

_lock = threading.Lock()

_DEFAULT_PATH = None  # resolved lazily; tests monkeypatch _state_path


def _state_path() -> Path:
    global _DEFAULT_PATH
    if _DEFAULT_PATH is None:
        import os

        env = os.environ.get("BLUE_ENFORCEMENT_FILE")
        if env:
            _DEFAULT_PATH = Path(env)
        else:
            from suijin.modules.platform.lib.constants import TMP_DIR

            _DEFAULT_PATH = TMP_DIR / "blue_enforcement.json"
    return _DEFAULT_PATH


_EMPTY = {"blocks": {}, "honeypots": {}, "fakes": {}, "redirects": {}, "canaries": {}, "canary_hits": []}


def _load() -> dict:
    try:
        data = json.loads(_state_path().read_text())
        merged = dict(_EMPTY)
        for k in _EMPTY:
            if k in data:
                merged[k] = data[k]
        return merged
    except (OSError, ValueError):
        return {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in _EMPTY.items()}


def _save(state: dict) -> None:
    try:
        _state_path().parent.mkdir(parents=True, exist_ok=True)
        _state_path().write_text(json.dumps(state, indent=2))
    except OSError:
        pass


# ── blocks ──────────────────────────────────────────────────────────────


def block_ip(ip: str, reason: str = "") -> str:
    with _lock:
        s = _load()
        s["blocks"][ip] = {"ts": time.time(), "reason": reason[:120]}
        _save(s)
    return f"BLOCKED {ip} at the proxy — next request gets 403 ({reason or 'no reason given'})"


def unblock_ip(ip: str) -> str:
    with _lock:
        s = _load()
        if ip not in s["blocks"]:
            return f"{ip} was not blocked"
        del s["blocks"][ip]
        _save(s)
    return f"UNBLOCKED {ip}"


def blocked_ips() -> list:
    return sorted(_load()["blocks"].keys())


def is_blocked(ip: str) -> bool:
    return ip in _load()["blocks"]


# ── honeypots (serve fake content INSTEAD of forwarding) ───────────────


def serve_honeypot(path_prefix: str, content: str, content_type: str = "application/json", status: int = 200) -> str:
    if not str(path_prefix or "").startswith("/"):
        return "Error: path_prefix must start with /"
    with _lock:
        s = _load()
        s["honeypots"][path_prefix] = {
            "content": str(content)[:8000],
            "content_type": content_type,
            "status": int(status),
        }
        _save(s)
    return f"HONEYPOT armed at {path_prefix} — requests there get your crafted content, never the real app"


def remove_honeypot(path_prefix: str) -> str:
    with _lock:
        s = _load()
        if path_prefix not in s["honeypots"]:
            return f"no honeypot at {path_prefix}"
        del s["honeypots"][path_prefix]
        _save(s)
    return f"honeypot removed at {path_prefix}"


# ── fake responses / redirects ──────────────────────────────────────────


def fake_response(path_prefix: str, body: str, status: int = 200) -> str:
    if not str(path_prefix or "").startswith("/"):
        return "Error: path_prefix must start with /"
    with _lock:
        s = _load()
        s["fakes"][path_prefix] = {"body": str(body)[:8000], "status": int(status)}
        _save(s)
    return f"FAKE response armed at {path_prefix} (status {status})"


def redirect_ip(ip: str, url: str) -> str:
    with _lock:
        s = _load()
        s["redirects"][ip] = str(url)[:500]
        _save(s)
    return f"REDIRECT armed: {ip} -> {url}"


# ── canaries ────────────────────────────────────────────────────────────


def arm_canary(token: str, note: str = "") -> str:
    if not token or len(str(token)) < 6:
        return "Error: canary token too short (>=6 chars)"
    with _lock:
        s = _load()
        s["canaries"][str(token)] = {"note": note[:120], "ts": time.time()}
        _save(s)
    return f"CANARY armed ({str(token)[:6]}…) — any request containing it trips"


def _check_canaries(text: str, ip: str, path: str) -> None:
    """Proxy hook: record a hit when canary material appears in a request."""
    if not text:
        return
    s = _load()
    for token, meta in s["canaries"].items():
        if token in text:
            with _lock:
                s2 = _load()
                s2["canary_hits"].append(
                    {
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "ip": ip,
                        "path": path[:120],
                        "token": token[:8] + "…",
                        "note": meta.get("note", ""),
                    }
                )
                s2["canary_hits"] = s2["canary_hits"][-200:]
                _save(s2)
            return


def canary_hits() -> list:
    return list(_load()["canary_hits"])


# ── enforcement decision (the proxy calls this per-request) ─────────────


def enforce(method: str, path: str, ip: str, body: str = "") -> dict | None:
    """Returns an enforcement action dict, or None to forward normally.

    Action shapes:
      {"kind": "block"}                        -> 403
      {"kind": "redirect", "url": ...}         -> 302
      {"kind": "respond", "body": ..., "content_type": ..., "status": ...}
    """
    state = _load()
    if ip in state["blocks"]:
        return {"kind": "block"}
    if ip in state["redirects"]:
        return {"kind": "redirect", "url": state["redirects"][ip]}
    for prefix, hp in sorted(state["honeypots"].items(), key=lambda kv: -len(kv[0])):
        if path.startswith(prefix):
            return {
                "kind": "respond",
                "body": hp["content"],
                "content_type": hp.get("content_type", "application/json"),
                "status": hp.get("status", 200),
            }
    for prefix, fk in sorted(state["fakes"].items(), key=lambda kv: -len(kv[0])):
        if path.startswith(prefix):
            return {
                "kind": "respond",
                "body": fk["body"],
                "content_type": "text/plain",
                "status": fk.get("status", 200),
            }
    with contextlib.suppress(Exception):
        _check_canaries(body + " " + path, ip, path)
    return None


def snapshot() -> dict:
    """Full plane state (the /state surface)."""
    return _load()
