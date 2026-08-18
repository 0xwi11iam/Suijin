"""Custom detector rules + policy engine — operator governance, offline.

RULES (suijin/detector_rules.json): user-defined regex detectors merged
into the replay harness and battle watchdog (never silently into the
production TUI path — operators opt in per surface).
    [{"name": "...", "pattern": "...", "field": "body|path|ua|headers",
      "weight": 3, "type": "my_attack"}]

POLICY (suijin/policy.json): engagement guardrails enforced at the
dispatch chokepoint — allowed target scopes, blocked tools, blocked
argument patterns. `suijin policy check` lints both.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # suijin/
RULES_PATH = BASE_DIR / "detector_rules.json"
POLICY_PATH = BASE_DIR / "policy.json"

RULE_FIELDS = ("body", "path", "ua", "headers")
RULE_EXAMPLE = [
    {"name": "internal-admin-probe", "pattern": "/internal/admin", "field": "path", "weight": 4, "type": "recon"},
    {"name": "legacy-php-backdoor", "pattern": r"c99shell|r57shell", "field": "body", "weight": 5, "type": "webshell"},
]

_POLICY_DEFAULT = {
    "description": "Suijin engagement policy — edit as needed",
    "allowed_target_scopes": ["127.0.0.1", "localhost", "::1", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
    "blocked_tools": [],
    "blocked_arg_patterns": [],
}


# ── Detector rules ─────────────────────────────────────────────────────


def load_rules(path: Path | None = None) -> list[dict]:
    p = Path(path) if path else RULES_PATH
    if not p.exists():
        return []
    try:
        rules = json.loads(p.read_text())
        return [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []
    except ValueError:
        return []


def validate_rules(path: Path | None = None) -> list[str]:
    """Lint the rules file; returns a list of problems (empty = valid)."""
    p = Path(path) if path else RULES_PATH
    if not p.exists():
        return []
    problems: list[str] = []
    try:
        rules = json.loads(p.read_text())
    except ValueError as e:
        return [f"{p.name}: invalid JSON — {e}"]
    if not isinstance(rules, list):
        return [f"{p.name}: top level must be a JSON array of rule objects"]
    for i, r in enumerate(rules):
        where = f"rule[{i}]"
        if not isinstance(r, dict):
            problems.append(f"{where}: must be an object")
            continue
        name = r.get("name")
        if not name or not isinstance(name, str):
            problems.append(f"{where}: missing 'name'")
        if not r.get("pattern"):
            problems.append(f"{where}: missing 'pattern'")
        else:
            try:
                re.compile(r["pattern"])
            except re.error as e:
                problems.append(f"{where} ({name}): bad regex — {e}")
        field = r.get("field", "body")
        if field not in RULE_FIELDS:
            problems.append(f"{where} ({name}): field must be one of {RULE_FIELDS}")
        w = r.get("weight", 3)
        if not isinstance(w, (int, float)) or not 0 < w <= 10:
            problems.append(f"{where} ({name}): weight must be 1-10")
    return problems


def rule_field_text(entry: dict, field: str) -> str:
    return {
        "body": str(entry.get("body", "")),
        "path": str(entry.get("path", "")) + str(entry.get("query", "")),
        "ua": str(entry.get("user_agent", "")),
        "headers": json.dumps(entry.get("headers", {})),
    }.get(field, "")


def match_rules(entry: dict, rules: list[dict] | None = None) -> list[tuple[str, int]]:
    """(type, weight) signals fired by custom rules for one traffic entry."""
    out = []
    for r in rules if rules is not None else load_rules():
        try:
            if re.search(r.get("pattern", ""), rule_field_text(entry, r.get("field", "body"))):
                out.append((r.get("type", r.get("name", "custom")), int(r.get("weight", 3))))
        except re.error:
            continue
    return out


# ── Policy engine ──────────────────────────────────────────────────────


def load_policy(path: Path | None = None) -> dict:
    """Effective policy. ABSENT file = no policy (everything allowed) —
    governance is opt-in, so default behavior never changes for existing
    engagements. A present file is merged over the template defaults."""
    p = Path(path) if path else POLICY_PATH
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        merged = dict(_POLICY_DEFAULT)
        merged.update({k: v for k, v in data.items() if k in _POLICY_DEFAULT})
        return merged
    except ValueError:
        return {}


def validate_policy(path: Path | None = None) -> list[str]:
    p = Path(path) if path else POLICY_PATH
    if not p.exists():
        return []
    problems: list[str] = []
    try:
        data = json.loads(p.read_text())
    except ValueError as e:
        return [f"{p.name}: invalid JSON — {e}"]
    if not isinstance(data, dict):
        return [f"{p.name}: top level must be an object"]
    for key in ("blocked_tools", "allowed_target_scopes", "blocked_arg_patterns"):
        v = data.get(key)
        if v is None:
            continue
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            problems.append(f"{key}: must be a list of strings")
    for i, pat in enumerate(data.get("blocked_arg_patterns", [])):
        try:
            re.compile(pat)
        except re.error as e:
            problems.append(f"blocked_arg_patterns[{i}]: bad regex — {e}")
    return problems


_DNS_CACHE: dict[str, list[str]] = {}


def _resolve_host(host: str) -> list[str]:
    """Resolved IPs for a hostname (memoized). Raises OSError on DNS failure."""
    import socket

    if host not in _DNS_CACHE:
        infos = socket.getaddrinfo(host, None)
        _DNS_CACHE[host] = sorted({i[4][0] for i in infos})
    return _DNS_CACHE[host]


def _target_in_scope(host: str, scopes: list[str], policy: dict) -> tuple[bool, str]:
    """Scope check with DNS pinning: a hostname in scope must ALSO resolve
    only to in-scope IPs — otherwise 'example.com' could be scoped while its
    DNS points the agent at an arbitrary production box. Unresolvable names
    fail CLOSED unless the policy sets allow_unresolvable (offline labs)."""
    import ipaddress

    try:
        ipaddress.ip_address(host)
        return _ip_in_scope(host, scopes), ""
    except ValueError:
        if not _ip_in_scope(host, scopes):
            return False, ""
        try:
            ips = _resolve_host(host)
        except OSError:
            if policy.get("allow_unresolvable"):
                return True, ""
            return False, f"'{host}' does not resolve (DNS) and allow_unresolvable is not set"
        bad = [ip for ip in ips if not _ip_in_scope(ip, scopes)]
        if bad:
            return False, f"'{host}' resolves to out-of-scope IP(s): {', '.join(bad)}"
        return True, ""


def _ip_in_scope(host: str, scopes: list[str]) -> bool:
    import ipaddress

    try:
        addr = ipaddress.ip_address(host.strip())
    except ValueError:
        return any(host == s or host.endswith("." + s) for s in scopes if "/" not in s)
    for s in scopes:
        try:
            if "/" in s and addr in ipaddress.ip_network(s, strict=False):
                return True
            if "/" not in s:
                try:
                    if addr == ipaddress.ip_address(s):
                        return True
                except ValueError:
                    continue
        except (ValueError, TypeError):
            continue
    return False


def extract_target(args: dict) -> str | None:
    """Best-effort target host from tool args."""
    from urllib.parse import urlparse

    for key in ("target", "url", "host", "endpoint"):
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            u = urlparse(v if "://" in v else f"http://{v}")
            return u.hostname or v.split("/")[0].split(":")[0]
    return None


# Intel-only tools never touch the target — scope gating them would block
# harmless lookups on out-of-scope hostnames (e.g. building a dossier).
_SCOPE_EXEMPT = {
    "target_dossier",
    "search_kb",
    "search_cve",
    "check_knowledge",
    "kb_read",
    "kb_stats",
    "suggest_exploit",
    "find_wordlist",
    "web_search",
    "mine_failures",
    "list_skills",
    "list_own_files",
}


def check_policy(tool: str, args: dict, policy: dict | None = None) -> tuple[bool, str]:
    """Enforce policy at the dispatch chokepoint. Returns (allowed, reason)."""
    pol = policy or load_policy()
    if tool in pol.get("blocked_tools", []):
        return False, f"policy: tool '{tool}' is blocked by engagement policy"
    for pat in pol.get("blocked_arg_patterns", []):
        try:
            m = re.search(pat, json.dumps(args, default=str))
        except re.error:
            continue
        if m:
            return False, f"policy: args match blocked pattern '{pat}'"
    host = extract_target(args or {})
    if host and tool not in _SCOPE_EXEMPT and pol.get("allowed_target_scopes"):
        ok, why = _target_in_scope(host, pol["allowed_target_scopes"], pol)
        if not ok:
            detail = f" — {why}" if why else ""
            return False, (
                f"policy: target '{host}' is outside allowed scopes "
                f"({len(pol['allowed_target_scopes'])} configured){detail}"
            )
    return True, ""
