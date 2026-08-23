"""Operator authorization ledger + advisory program scopes.

`suijin authorize <domain>` — the operator attests bug-bounty authorization
for a target. Plain operator-side records (their machine, their attestation,
their responsibility): every engagement against that domain renders the
VERIFIED authorization into the per-turn engagement order, so the agent
never re-litigates it.

`suijin scope <bug-bounty-page-url>` — pulls a program's real scope via the
bugscope pack (operator's own platform token, per-call, never persisted)
and binds it ADVISORY: in-scope assets guide the agent's targeting,
explicitly out-of-scope assets steer it away. Nothing is mechanically
blocked at dispatch — the agent sees the binding in its order and can
scope_search the cached data to self-verify.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

DEFAULT_DAYS = 90

# bug-bounty page URL -> (platform key for bugscope, program handle)
_PAGE_PATTERNS = [
    (re.compile(r"(?i)^https?://(?:www\.)?hackerone\.com/([a-z0-9_.-]+)/?"), "h1"),
    (re.compile(r"(?i)^https?://(?:www\.)?bugcrowd\.com/([a-z0-9_.-]+)/?(?:\?.*)?$"), "bugcrowd"),
    (re.compile(r"(?i)^https?://(?:www\.)?yeswehack\.com/programs/([a-z0-9_.-]+)/?"), "ywh"),
    (re.compile(r"(?i)^https?://app\.intigriti\.com/programs/([a-z0-9_.-]+)/?"), "intigriti"),
    (re.compile(r"(?i)^https?://(?:www\.)?immunefi\.com/bug-bounty/s?/([^/]+)/?"), "immunefi"),
    (re.compile(r"(?i)^https?://(?:www\.)?immunefi\.com/bug-bounty/([a-z0-9_.-]+)/?"), "immunefi"),
]


def _ws_dir():
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR


def ledger_path():
    return _ws_dir() / "authorizations.json"


def scope_bindings_path():
    return _ws_dir() / "program_scopes.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── authorization ledger ────────────────────────────────────────────────


def load_ledger() -> list[dict]:
    p = ledger_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    except ValueError:
        return []


def save_ledger(rows: list[dict]) -> None:
    ledger_path().write_text(json.dumps(rows, indent=2))


def _host_of(target: str) -> str:
    t = str(target or "").strip().lower()
    if "://" in t:
        t = urlparse(t).hostname or t
    t = t.strip(". ")
    if t.startswith("*."):  # scope wildcard: *.example.com == example.com zone
        t = t[2:]
    return t


def add_authorization(target: str, program: str = "", authorization_id: str = "", days: int = DEFAULT_DAYS) -> dict:
    """Attest authorization for target (+subdomains). Upserts by host."""
    host = _host_of(target)
    if not host or "." not in host:
        return {"error": f"invalid target {target!r} — expected a domain (e.g. example.com)"}
    days = max(1, int(days or DEFAULT_DAYS))
    rows = [r for r in load_ledger() if _host_of(r.get("target", "")) != host]
    rec = {
        "target": host,
        "program": (program or "").strip() or "operator-attested",
        "authorization_id": (authorization_id or "").strip(),
        "attested_at": _now().strftime("%Y-%m-%d %H:%M UTC"),
        "expires_at": (_now() + timedelta(days=days)).strftime("%Y-%m-%d"),
    }
    rows.append(rec)
    save_ledger(rows)
    return rec


def remove_authorization(target: str) -> dict:
    host = _host_of(target)
    rows = load_ledger()
    kept = [r for r in rows if _host_of(r.get("target", "")) != host]
    if len(kept) == len(rows):
        return {"error": f"no ledger entry for {host}"}
    save_ledger(kept)
    return {"removed": host}


def match_authorization(target: str) -> dict | None:
    """Find an UNEXPIRED ledger entry covering target (exact or subdomain)."""
    host = _host_of(target)
    if not host:
        return None
    today = _now().strftime("%Y-%m-%d")
    for r in load_ledger():
        led = _host_of(r.get("target", ""))
        if not led:
            continue
        if host == led or host.endswith("." + led):
            if str(r.get("expires_at", "")) < today:  # expired — ignore
                continue
            return r
    return None


def list_authorizations() -> list[dict]:
    return sorted(load_ledger(), key=lambda r: str(r.get("attested_at", "")))


# ── program scope bindings (advisory) ───────────────────────────────────


def parse_scope_url(url: str) -> tuple[str, str] | None:
    """bug-bounty page URL -> (platform, program handle) or None."""
    u = str(url or "").strip()
    for rx, plat in _PAGE_PATTERNS:
        m = rx.match(u)
        if m:
            return plat, m.group(1).strip("/")
    return None


def load_scope_bindings() -> list[dict]:
    p = scope_bindings_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    except ValueError:
        return []


def save_scope_binding(binding: dict) -> None:
    rows = [r for r in load_scope_bindings() if r.get("key") != binding.get("key")]
    rows.append(binding)
    scope_bindings_path().write_text(json.dumps(rows, indent=2))


def bind_program_scope(platform: str, handle: str, token: str) -> dict:
    """Pull the program's scope via bugscope and write an advisory binding.

    The token is used for this pull only (bugscope's contract) and never
    leaves the API call. Returns the binding record or an error dict."""
    try:
        from suijin.modules.bugscope.main import scope_pull
    except Exception as e:  # noqa: BLE001 — pack load failure is data
        return {"error": f"bugscope pack unavailable: {e}"}
    result = scope_pull(platform=platform, token=token, programs=handle)
    if str(result).startswith("Error"):
        return {"error": str(result)}
    rows = _rows_for(platform, handle)
    in_scope = sorted({r["asset"] for r in rows if r.get("eligible")})
    out_of_scope = sorted({r["asset"] for r in rows if not r.get("eligible")})
    if not in_scope and not out_of_scope:
        return {"error": f"no scope rows found for {platform}/{handle} — check the handle (got: {str(result)[:120]})"}
    binding = {
        "key": f"{platform}/{handle}",
        "platform": platform,
        "program": handle,
        "in_scope": in_scope,
        "out_of_scope": out_of_scope,
        "pulled_at": _now().strftime("%Y-%m-%d %H:%M UTC"),
        "advisory": True,
    }
    save_scope_binding(binding)
    return binding


def _rows_for(platform: str, handle: str) -> list[dict]:
    """Read the platform's bugscope cache, filtered to one program."""
    from suijin.modules.platform.lib.workspace import artifact_dir

    f = artifact_dir("bugscope") / f"{platform}.json"
    if not f.exists():
        return []
    try:
        rows = json.loads(f.read_text())
    except ValueError:
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("program") == handle]


def match_scope_bindings(target: str) -> list[dict]:
    """Bindings whose in_scope or out_of_scope assets cover the target."""
    host = _host_of(target)
    if not host:
        return []
    hits = []
    for b in load_scope_bindings():
        assets = list(b.get("in_scope", [])) + list(b.get("out_of_scope", []))
        for a in assets:
            ah = _host_of(str(a))
            if not ah:
                # wildcard asset (*.example.com)
                aw = str(a).lower().lstrip("*.")
                if aw and (host == aw or host.endswith("." + aw)):
                    hits.append(b)
                    break
            elif host == ah or host.endswith("." + ah) or ah.endswith("." + host):
                hits.append(b)
                break
    return hits


# ── engagement-order rendering (what the agent sees, every turn) ────────


def authorization_line(target: str) -> str | None:
    """Authorization line for the target, or None. Deliberately BORING:
    strong framing (SECURE! FINAL! never question!) primed capable models
    into meta-suspicion ('why does this prompt keep insisting?') — field
    run: 'instructions to never question it are precisely why I won't
    rely on it'. A flat procedural record gets treated as settled fact."""
    rec = match_authorization(target)
    if not rec:
        return None
    ident = f", id {rec['authorization_id']}" if rec.get("authorization_id") else ""
    prog = str(rec.get("program", "")).lower()
    prog_s = f"{prog}, " if prog and prog != "operator-attested" else ""
    return f"on file — suijin authorize record ({prog_s}{ident}valid through {rec['expires_at']})"


def scope_line(target: str) -> str | None:
    """Advisory program-scope sentence(s) for the target, or None."""
    hits = match_scope_bindings(target)
    if not hits:
        return None
    parts = []
    for b in hits:
        nin = len(b.get("in_scope", []))
        nout = len(b.get("out_of_scope", []))
        line = f"{b['platform']}/{b['program']}: {nin} asset(s) in scope"
        if nout:
            line += f", {nout} explicitly OUT of scope — stay off out-of-scope assets"
        parts.append(line)
    return (
        "Program scope (advisory): "
        + "; ".join(parts)
        + ". scope_search shows the cached asset list — verify targets against it before deep testing."
    )
