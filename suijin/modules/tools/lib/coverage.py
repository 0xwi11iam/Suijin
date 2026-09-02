"""Coverage ledger — the untested-cell accounting (Wave 4).

A (asset × vuln-class) table the agent both writes and reads: every
think turn the context shows the top UNTESTED cells; wide/local notes
record mechanism facts once per origin. The completion gate refuses
model-initiated closure while untried surfaces or uncovered cells
remain — "tested 3 endpoints and declared victory" becomes structurally
impossible (the field-review hole).

Evidence discipline: tested_not_vulnerable claims REQUIRE evidence
(a sent request + a response summary) — a note without a send is a
FALSE record that makes later checks skip a real vulnerability.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

_LOCK = threading.Lock()

# the vuln-class lanes (WSTG-condensed)
CLASSES = (
    "idor", "authz", "authn", "mass_assignment", "sqli", "xss", "ssti",
    "cmdi", "ssrf", "lfi", "upload", "xxe", "race", "redirect", "info",
)

_NOTES: dict[str, list] = {}  # "wide" | "local" -> list of note dicts


def _store_path() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR, engagement_dir

    base = Path(engagement_dir() or (WORKSPACE_DIR / "outputs" / "engagements" / "_default"))
    return base / "coverage.json"


def _load() -> dict:
    try:
        with open(_store_path()) as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001 — fresh engagement = fresh ledger
        return {"cells": {}, "notes": {"wide": [], "local": []}}


def _save(d: dict) -> None:
    try:
        p = _store_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, open(p, "w") as fh:
            json.dump(d, fh, indent=2)
    except Exception:  # noqa: BLE001 — accounting never breaks a run
        pass


def asset_of(url_or_path: str) -> str:
    """Asset key: origin for wide concerns, method+shape for endpoints."""
    s = str(url_or_path or "")
    if "://" in s:
        parts = urlsplit(s)
        tail = re.sub(r"/([^/]*\d[^/]*)$", "/:id", parts.path)
        return f"{parts.scheme}://{parts.netloc}{tail}"
    return s


def mark(asset: str, vuln_class: str, status: str, evidence: str = "", request_sent: str = "") -> str:
    """status: tested_vulnerable | tested_not_vulnerable | not_applicable.
    not_vulnerable REQUIRES evidence — the false-record guard."""
    try:
        vc = str(vuln_class).lower().strip()
        if vc not in CLASSES:
            return f"Error: class '{vc}' not in the lanes ({', '.join(CLASSES)})"
        st = str(status).lower().strip()
        if st not in ("tested_vulnerable", "tested_not_vulnerable", "not_applicable"):
            return "Error: status must be tested_vulnerable | tested_not_vulnerable | not_applicable"
        if st == "tested_not_vulnerable":
            if not str(evidence).strip() or len(str(evidence).strip()) < 30:
                return ("Error: tested_not_vulnerable REQUIRES evidence (≥30 chars: what you sent, what came back, "
                        "why it holds). A note without a sent request is a FALSE record — it makes later checks SKIP "
                        "this class and hides a real vulnerability.")
            if not str(request_sent).strip():
                return "Error: tested_not_vulnerable requires request_sent (the verb/command you actually fired)."
        d = _load()
        key = f"{asset_of(asset)}|{vc}"
        d["cells"][key] = {"asset": asset_of(asset), "vuln_class": vc, "status": st,
                           "evidence": str(evidence)[:400], "request_sent": str(request_sent)[:200]}
        _save(d)
        return f"coverage: {asset_of(asset)} · {vc} → {st}"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def note(kind: str, subject: str, text: str) -> str:
    """wide (origin-wide mechanism fact, recorded ONCE) | local (endpoint)."""
    try:
        k = str(kind).lower().strip()
        if k not in ("wide", "local"):
            return "Error: kind must be wide | local"
        d = _load()
        entry = {"subject": asset_of(subject) if k == "wide" else str(subject), "text": str(text)[:300]}
        if not any(n["text"] == entry["text"] for n in d["notes"][k]):
            d["notes"][k].append(entry)
            _save(d)
        return f"{k} note recorded: {str(text)[:60]}"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def untested(assets: list[str], limit: int = 5) -> list[dict]:
    """The priority untested list for the given assets (origin or shape)."""
    try:
        d = _load()
        done = {k: c["status"] for k, c in d["cells"].items()}
        out = []
        for a in assets[:20]:
            a_key = asset_of(a)
            for vc in CLASSES:
                st = done.get(f"{a_key}|{vc}")
                if st is None or st == "pending":
                    out.append({"asset": a_key, "vuln_class": vc})
        return out[:limit]
    except Exception:  # noqa: BLE001
        return []


def summary() -> str:
    try:
        d = _load()
        cells = d.get("cells") or {}
        counts: dict[str, int] = {}
        for c in cells.values():
            counts[c["status"]] = counts.get(c["status"], 0) + 1
        wide = d.get("notes", {}).get("wide") or []
        lines = [f"COVERAGE: {len(cells)} cells recorded ({', '.join(f'{k}={v}' for k, v in sorted(counts.items())) or 'none'})"]
        if wide:
            lines.append("WIDE NOTES (origin-wide facts — do NOT re-derive per endpoint):")
            lines.extend(f"  - {n['subject']}: {n['text'][:120]}" for n in wide[-5:])
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return ""


def completion_blocked(assets: list[str]) -> str | None:
    """The completion gate: closure is refused while priority cells are
    untested — returns the refusal directive or None."""
    ute = untested(assets, limit=999)
    if len(ute) <= 3:  # a residue of 3 is completion noise, not avoidance
        return None
    top = ", ".join(f"{u['vuln_class']}@{u['asset'].split('//')[-1][:30]}" for u in ute[:4])
    return (
        f"COMPLETION REFUSED (coverage gate): {len(ute)} high-priority vuln-class cells remain untested "
        f"(top: {top}). Mark each tested_vulnerable / tested_not_vulnerable (with evidence) / not_applicable "
        "via coverage_check, or state in your thought why the remaining classes cannot apply — "
        "then complete. 'Tested 3 endpoints and declared victory' is the failure mode this gate exists for."
    )


def coverage_check(action: str = "summary", asset: str = "", vuln_class: str = "", status: str = "",
                   evidence: str = "", request_sent: str = "", kind: str = "", subject: str = "",
                   text: str = "", assets: list | None = None) -> str:
    """The ledger tool: mark cells (with evidence), record wide/local
    notes, or read the summary/untested list."""
    try:
        act = str(action or "summary").lower()
        if act == "mark":
            return mark(asset, vuln_class, status, evidence, request_sent)
        if act == "note":
            return note(kind or "local", subject or asset, text)
        if act == "untested":
            return json.dumps(untested(assets or [asset], limit=10), indent=2)
        if act == "gate":
            blocked = completion_blocked(assets or [asset])
            return blocked or "gate: OPEN — coverage sufficient for completion."
        return summary()
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"
