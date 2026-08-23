"""Engagement state board — the agent's working memory, made real.

target_info was set once at init and never written again (the agent
re-derived its own attack surface every ~8 actions), tested_axes was
tracked but never shown, and background jobs were invisible unless the
agent remembered to job_list. This module:

  - extracts board updates from tool outputs (ports, services, tech,
    endpoints, creds, subdomains) at the execute seam
  - merges them into target_info (deduped, capped to protect the prompt)
  - renders a compact board for the every-turn context block, with the
    coverage map and running jobs

Everything here is pure + stdlib; the extractors are deliberately
pattern-based (no parsing of arbitrary output into execution).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# caps per board field — the board must stay prompt-cheap
_CAP = 60

# nmap-ish lines: "443/tcp  open  https  nginx 1.18.0" / "8080/open//http"
_NMAP_PORT = re.compile(r"\b(\d{1,5})/(tcp|udp)\s+(open|open\|filtered)\s*(\S*)\s*([^\n]*)")
# tech from headers / whatweb-ish lines
_HDR_SERVER = re.compile(
    r"(?i)['\"]?server['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9._\- /]{2,40})",
)
_HDR_POWERED = re.compile(r"(?i)x-powered-by['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9._\- /]{2,40})")
_URL_PATH = re.compile(r"(?i)\b(?:https?://[^\s\"'<>]+)")
_CRED_RES = [
    ("AWS key", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
    ("GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,})\b")),
    ("OpenAI-style key", re.compile(r"\b(sk-[A-Za-z0-9_-]{20,})\b")),
    ("Google API key", re.compile(r"\b(AIza[0-9A-Za-z_-]{35})\b")),
    ("Slack token", re.compile(r"\b(xox[abprs]-[A-Za-z0-9-]{10,})\b")),
    ("JWT", re.compile(r"\b(eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b")),
]
_SUBDOMAIN_LINE = re.compile(r"(?i)^\s*([a-z0-9_-]+(?:\.[a-z0-9_-]+)+)\s*$", re.M)


def _clip(items: list, cap: int = _CAP) -> list:
    return items[:cap]


def extract_from_output(tool_name: str, tool_args: dict, output: str) -> dict:
    """Pattern-extract board updates from one tool result. Returns a partial
    target_info dict (empty when nothing was learned)."""
    out = str(output or "")
    args = tool_args or {}
    upd: dict = {}

    ports: list[int] = []
    services: list[str] = []
    if tool_name in ("execute_terminal", "nmap", "recon_chain", "job_output", "job_wait"):
        for m in _NMAP_PORT.finditer(out):
            try:
                p = int(m.group(1))
            except ValueError:
                continue
            if 1 <= p <= 65535 and p not in ports:
                ports.append(p)
            tail = " ".join((m.group(4) or "", m.group(5) or "")).strip()
            for tok in tail.split():
                tok = tok.strip("(),")
                if (
                    2 <= len(tok) <= 24
                    and re.match(r"^[a-zA-Z][\w.\-]*[\w.\-]$", tok)
                    and tok.lower() not in ("open", "filtered", "tcp", "udp")
                    and tok not in services
                ):
                    services.append(tok)
        if ports:
            upd["ports"] = ports
        if services:
            upd["services"] = services[:_CAP]

    tech: list[str] = []
    if tool_name in ("http_request", "whatweb", "whatweb_scan", "httpx_probe", "techfp"):
        for rx in (_HDR_SERVER, _HDR_POWERED):
            for m in rx.finditer(out):
                t = m.group(1).strip(" '\"")
                if t and t not in tech:
                    tech.append(t)
        # whatweb-style "nginx[1.18.0], PHP[7.4]"
        for m in re.finditer(r"\b([A-Za-z][A-Za-z0-9_.\-]{1,20})\[([0-9][0-9A-Za-z.\-]{0,14})\]", out):
            t = f"{m.group(1)} {m.group(2)}"
            if t not in tech:
                tech.append(t)
        if tech:
            upd["technologies"] = _clip(tech)

    # endpoints: URLs we hit with a real path (root hits are noise) +
    # js-bundle ROUTES section
    endpoints: list[str] = []
    url = str(args.get("url") or "")
    if url.startswith(("http://", "https://")):
        u = urlparse(url)
        if u.path and u.path != "/":
            ep = f"{u.hostname}{(':' + str(u.port)) if u.port else ''}{u.path}"
            endpoints.append(ep)
    if tool_name in ("js_bundle_analyze", "openapi_parse", "openapi_find", "parse_sitemap", "graphql_introspect"):
        for line in out.splitlines():
            s = line.strip().lstrip("+ ").strip()
            if s.startswith("/") and 1 < len(s) <= 80 and " " not in s[:1]:
                endpoints.append(s)
        # bundle "  /api/v1/login" indented entries
        for m in re.finditer(r"^\s{2,}(/\w[\w/{}$.\-]{1,60})\s*$", out, re.M):
            endpoints.append(m.group(1))
    if endpoints:
        seen: list[str] = []
        for e in endpoints:
            if e not in seen:
                seen.append(e)
        upd["endpoints"] = _clip(seen)

    creds: list[dict] = []
    for kind, rx in _CRED_RES:
        for m in rx.findall(out):
            v = m.strip()
            if not any(c["value"] == v for c in creds):
                creds.append({"kind": kind, "value": v[:60]})
    if creds:
        upd["credentials"] = _clip(creds)

    if tool_name in ("crtsh_subdomains", "subfinder_enum", "dns_brute", "crtsh_domain"):
        subs = []
        for m in _SUBDOMAIN_LINE.finditer(out):
            s = m.group(1).lower().strip(".")
            if s and s not in subs:
                subs.append(s)
        if subs:
            upd["subdomains"] = _clip(subs)

    return upd


def merge_updates(target_info: dict, updates: dict) -> tuple[dict, bool]:
    """Merge partial updates into a board dict. Returns (merged, grew)."""
    board = dict(target_info or {})
    grew = False
    for key, items in (updates or {}).items():
        if not isinstance(items, list):
            continue
        cur = list(board.get(key) or [])
        if key == "credentials":
            for c in items:
                if isinstance(c, dict) and not any(
                    x.get("value") == c.get("value") for x in cur if isinstance(x, dict)
                ):
                    cur.append(c)
        else:
            for v in items:
                if v not in cur:
                    cur.append(v)
        cur = cur[-_CAP:] if key == "credentials" else cur[:_CAP]
        if len(cur) != len(list(board.get(key) or [])):
            grew = True
        board[key] = cur
    return board, grew


def _fmt(items, limit=8) -> str:
    items = list(items or [])
    if not items:
        return "(none yet)"
    if len(items) <= limit:
        return ", ".join(str(i) for i in items)
    return ", ".join(str(i) for i in items[:limit]) + f" (+{len(items) - limit} more)"


def render_board(target_info: dict, tested_axes: dict | None = None, running_jobs: list | None = None) -> str:
    """The compact board for the think-node context block."""
    t = target_info or {}
    lines = []
    if t.get("primary_target"):
        lines.append(f"target: {t['primary_target']}")
    if t.get("ports"):
        lines.append(f"ports ({len(t['ports'])}): {_fmt(t['ports'])}")
    if t.get("services"):
        lines.append(f"services ({len(t['services'])}): {_fmt(t['services'])}")
    if t.get("technologies"):
        lines.append(f"tech ({len(t['technologies'])}): {_fmt(t['technologies'])}")
    if t.get("endpoints"):
        lines.append(f"endpoints ({len(t['endpoints'])}): {_fmt(t['endpoints'], 10)}")
    if t.get("subdomains"):
        lines.append(f"subdomains ({len(t['subdomains'])}): {_fmt(t['subdomains'])}")
    if t.get("credentials"):
        creds = ", ".join(f"{c.get('kind')}: {str(c.get('value'))[:20]}…" for c in t["credentials"][:6])
        lines.append(f"credentials ({len(t['credentials'])}): {creds}")
    if not lines:
        lines.append("(nothing recorded yet — recon outputs populate this board)")

    axes = tested_axes or {}
    if axes:
        tried = sorted(axes.items(), key=lambda kv: -kv[1].get("attempts", 0))[:8]
        ax = ", ".join(f"{k}({v.get('attempts', 0)}a/{v.get('failures', 0)}f)" for k, v in tried)
        lines.append(f"tested axes: {ax}")

    jobs = running_jobs or []
    if jobs:
        lines.append(
            f"RUNNING background jobs: {', '.join(str(j) for j in jobs[:6])} — read them with job_output when done"
        )

    return "\n".join(lines)
