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
    "idor",
    "authz",
    "authn",
    "mass_assignment",
    "sqli",
    "xss",
    "ssti",
    "cmdi",
    "ssrf",
    "lfi",
    "upload",
    "xxe",
    "race",
    "redirect",
    "info",
)

#: 2026-10-20 (go-bigger): cells below this weight never block
#: completion — imported from the weaponization engine (single source
#: of truth for impact weights).
try:
    from suijin.modules.agent.lib.weaponization import BLOCKING_MIN_WEIGHT  # noqa: F401
except Exception:  # noqa: BLE001 — bridge-less contexts keep a sane default
    BLOCKING_MIN_WEIGHT = 5

_NOTES: dict[str, list] = {}  # "wide" | "local" -> list of note dicts


def reset() -> None:
    """Engagement scope: a fresh run starts with a fresh ledger — stale
    coverage notes from a previous target kept tripping the completion
    gate of the next."""
    with _LOCK:
        _NOTES.clear()


def _store_path() -> Path:
    from suijin.modules.platform.lib.workspace import engagement_dir

    base = Path(engagement_dir())
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
                return (
                    "Error: tested_not_vulnerable REQUIRES evidence (≥30 chars: what you sent, what came back, "
                    "why it holds). A note without a sent request is a FALSE record — it makes later checks SKIP "
                    "this class and hides a real vulnerability."
                )
            if not str(request_sent).strip():
                return "Error: tested_not_vulnerable requires request_sent (the verb/command you actually fired)."
        # A (2026-10-20, go-bigger): not_applicable on a HIGH-WEIGHT class
        # requires justification — the gate counts those cells as
        # completion-blocking, so a bare N/A was a free gate-bypass on
        # the crown jewels (authn/sqli/cmdi...). Trivia cells stay free.
        if st == "not_applicable":
            from suijin.modules.agent.lib.weaponization import class_weight

            if class_weight(vc) >= 5 and len(str(evidence).strip()) < 50:
                return (
                    f"Error: not_applicable on a high-weight class ({vc}) REQUIRES justification (≥50 chars: "
                    "WHY this class cannot exist on this asset — no auth flow, no user input, no server-side "
                    "rendering, etc.). 'Doesn't apply' without a reason is avoidance, not a verdict. "
                    "(Low-weight classes — info/redirect — mark freely.)"
                )
        d = _load()
        key = f"{asset_of(asset)}|{vc}"
        d["cells"][key] = {
            "asset": asset_of(asset),
            "vuln_class": vc,
            "status": st,
            "evidence": str(evidence)[:400],
            "request_sent": str(request_sent)[:200],
        }
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
    """The priority untested list for the given assets (origin or shape).
    Ordered by class weight DESC (2026-10-20): the agent sees authn/
    authz/idor-class gaps first, trivia last — the equal-count list sent
    it curling info cells because they sorted the same as the crown
    jewels."""
    try:
        from suijin.modules.agent.lib.weaponization import class_weight

        d = _load()
        done = {k: c["status"] for k, c in d["cells"].items()}
        out = []
        for a in assets[:20]:
            a_key = asset_of(a)
            for vc in CLASSES:
                st = done.get(f"{a_key}|{vc}")
                if st is None or st == "pending":
                    out.append({"asset": a_key, "vuln_class": vc, "weight": class_weight(vc)})
        out.sort(key=lambda u: u.get("weight", 0), reverse=True)
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
        lines = [
            f"COVERAGE: {len(cells)} cells recorded ({', '.join(f'{k}={v}' for k, v in sorted(counts.items())) or 'none'})"
        ]
        if wide:
            lines.append("WIDE NOTES (origin-wide facts — do NOT re-derive per endpoint):")
            lines.extend(f"  - {n['subject']}: {n['text'][:120]}" for n in wide[-5:])
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return ""


def completion_blocked(assets: list[str]) -> str | None:
    """The completion gate: closure is refused while priority cells are
    untested — returns the refusal directive or None.

    Weight-aware (2026-10-20): only weight ≥ BLOCKING_MIN_WEIGHT cells
    block. info (1) and redirect (2) are recon output, not obligations —
    the agent can no longer buy completion with trivia cells, and trivia
    cells can no longer pad the refusal count."""
    ute_all = untested(assets, limit=999)
    ute = [u for u in ute_all if u.get("weight", 5) >= BLOCKING_MIN_WEIGHT]
    if len(ute) <= 3:  # a residue of 3 is completion noise, not avoidance
        return None
    top = ", ".join(f"{u['vuln_class']}@{u['asset'].split('//')[-1][:30]} (w{u.get('weight', '?')})" for u in ute[:4])
    return (
        f"COMPLETION REFUSED (coverage gate): {len(ute)} high-impact vuln-class cells remain untested "
        f"(top: {top}). Mark each tested_vulnerable / tested_not_vulnerable (with evidence) / not_applicable "
        "via coverage_check, or state in your thought why the remaining classes cannot apply — "
        "then complete. 'Tested 3 endpoints and declared victory' is the failure mode this gate exists for."
    )


def coverage_check(
    action: str = "summary",
    asset: str = "",
    vuln_class: str = "",
    status: str = "",
    evidence: str = "",
    request_sent: str = "",
    kind: str = "",
    subject: str = "",
    text: str = "",
    assets: list | None = None,
) -> str:
    """The ledger tool: mark cells (with evidence), record wide/local
    notes, or read the summary/untested list.

    2026-10-20 (F-round): the TOOL CONTRACT's enum (tested_vulnerable /
    tested_not_vulnerable / not_applicable as ACTION values) disagreed
    with this implementation's verbs (mark/note/untested/gate) — the
    agent literally could not mark a cell: action=mark failed contract
    validation, action=not_applicable fell through to summary. Both
    vocabularies now work; status-as-action implies mark."""
    try:
        act = str(action or "summary").lower()
        if act in ("tested_vulnerable", "tested_not_vulnerable", "not_applicable"):
            if not str(status).strip():
                status = act
            act = "mark"
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
