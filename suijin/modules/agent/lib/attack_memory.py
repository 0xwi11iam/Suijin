"""Positive memory + the chain planner.

what_worked(target): the reader the system never had — every prior
CONFIRMED exploit for this target, plus CLASS-LEVEL TRANSFER (which
vulnerability classes have paid anywhere, so a fresh target matching
a known stack family starts with proven attack priorities). Everything
else in the memory stack is negative (blocks/failures/false-positives);
this is the positive half.

plan_chains(state): deterministic next-step rules over the live state —
ingredients the agent already holds (credentials, admin tokens, upload
endpoints, confirmed findings) composed into ready attack chains.
Advisory context ("CHAINS READY:"), procedural tone.
"""

from __future__ import annotations

import json
import re

_HOST_RX = re.compile(r"https?://([a-z0-9.\-]+:\d+|[a-z0-9.\-]+)", re.I)
_DOMAIN_RX = re.compile(r"\b([a-z0-9\-]+\.(?:com|net|org|io|corp|dev|local|example|me|co)(?::\d+)?)\b", re.I)


def target_key(text: str) -> str:
    """The durable target key: host[:port] from the objective text — NOT the
    objective prose (the old keying made every re-worded run a new target)."""
    t = str(text or "")
    m = _HOST_RX.search(t)
    if m:
        return m.group(1).lower().rstrip(".")
    m = _DOMAIN_RX.search(t)
    if m:
        return m.group(1).lower()
    return t.strip().lower()[:60]


def _catalog_root():
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR / "exploits"


def what_worked(target: str, limit: int = 8) -> list[str]:
    """Lines of prior CONFIRMED exploits for this target, then class-level
    transfer (classes that paid on other targets). Never raises."""
    out: list[str] = []
    try:
        key = target_key(target)
        same: list[str] = []
        class_hits: dict[str, int] = {}
        for eng_dir in sorted(_catalog_root().glob("*"), reverse=True):
            idx = eng_dir / "catalog.json"
            if not idx.exists():
                continue
            try:
                index = json.loads(idx.read_text())
            except Exception:  # noqa: BLE001 — corrupt catalogs are skipped
                continue
            for e in index.get("entries") or []:
                if str(e.get("status", "")).upper() != "CONFIRMED":
                    continue
                cls = str(e.get("class") or e.get("vuln_class") or "?")
                if target_key(str(e.get("target") or "")) == key:
                    same.append(f"{cls}: {str(e.get('title', ''))[:70]} ({e.get('id', '?')})")
                else:
                    class_hits[cls] = class_hits.get(cls, 0) + 1
        if same:
            out.append("PRIOR CONFIRMED on this target:")
            out.extend(f"  ✓ {s}" for s in same[:limit])
        if class_hits:
            top = sorted(class_hits.items(), key=lambda kv: -kv[1])[:4]
            out.append("Class transfer (paid on other targets, untried here): " + ", ".join(f"{c}×{n}" for c, n in top))
        return out
    except Exception:  # noqa: BLE001
        return out


# ── chain planner ────────────────────────────────────────────────────


def plan_chains(state: dict, limit: int = 3) -> list[str]:
    """Deterministic ingredient→chain rules. Advisory, never raises."""
    try:
        board = state.get("target_info") or {}
        creds = board.get("credentials") or []
        endpoints = [str(e) for e in (board.get("endpoints") or [])][:40]
        blob = " ".join(endpoints).lower()
        blob_all = blob + " " + json.dumps(creds)[:400].lower()
        findings = [str(f.get("type") or f.get("class") or "") for f in (state.get("findings") or [])]
        chains: list[str] = []

        has_login = any(("login" in e or "auth" in e or "signin" in e) for e in endpoints)
        has_admin = "admin" in blob
        has_upload = "upload" in blob
        has_api = "/api" in blob
        jwt_cred = any("eyj" in str(c).lower()[:30] for c in creds) or "jwt" in blob_all

        if creds and has_login:
            chains.append(
                "captured credentials × login surface → replay creds on the login form and every other auth boundary (password reuse is the default failure mode)"
            )
        if jwt_cred and (has_admin or has_api):
            chains.append(
                "JWT/token captured × admin/API surface → decode claims, forge/upgrade the role claim, replay against privileged endpoints"
            )
        if has_upload:
            chains.append(
                "upload endpoint present → extension-blocklist bypass (.phtml/.pyc/renamed), locate the served path, verify execution"
            )
        if "ssrf" in " ".join(findings).lower() or "webhook" in blob:
            chains.append(
                "SSRF primitive → internal port sweep through it, cloud metadata routes, then chain returned creds into internal services"
            )
        if any("sql" in f.lower() for f in findings):
            chains.append(
                "confirmed SQLi → extraction discipline: column alignment, table enumeration, credential tables — data or it didn't happen"
            )
        if state.get("_foothold_at"):
            chains.append(
                "foothold held → privesc checklist (sudo -l, SUID, writable services), loot inventory, pivot surface mapping"
            )
        return chains[:limit]
    except Exception:  # noqa: BLE001
        return []
