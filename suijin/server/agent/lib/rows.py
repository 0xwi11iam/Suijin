"""Suijin state rows — facts, assets, intents the next turn READS (2026-11-03).

THE DIAGNOSIS THIS MODULE ANSWERS (operator's wall memo):
  "Memory is the chat. Compaction fires at 90% of the window and keeps 16
  messages. A fact that was not written down is deleted."
  "The successful path is not the path the tools actually allow."

ARTEX's insight, copied honestly: rows in the engagement directory that
outlive compaction, with ids the code computes and a snapshot the think
context starts with. Files, not Postgres. If it was not written, the
next turn does not know it — so every surface WRITES.

THE ROWS:
  fact    — one summary + evidence line + observed|inferred; one per
            INTENT, not one per curl (the anti-noise rule)
  asset   — root_domain/subdomain/ip/service/app/endpoint; code-computed
            ids; insert-twice returns the same id; parent links computed
  intent  — state open|running|done|exhausted|blocked; exhausted means
            the budget cut it (READ the trace and continue); done means
            do not retry. A thin negative gets one recheck, not a ban.
  finding — legal only if THIS RUN triggered it; statuses pending,
            confirmed, fixed, false_positive, ignored, duplicate

The snapshot (what the think context opens with): counts, last facts,
open intents, untested assets, class weights beside asset percent.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from pathlib import Path

# ── the store ─────────────────────────────────────────────────────────


def rows_dir() -> Path:
    """<engagement>/state/rows/ — one JSONL file per row kind."""
    from suijin.modules.platform.lib.workspace import state_dir

    d = state_dir() / "rows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(kind: str) -> Path:
    return rows_dir() / f"{kind}.jsonl"


def _append(kind: str, row: dict) -> dict:
    with contextlib.suppress(OSError), _file(kind).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return row


def _all(kind: str) -> list[dict]:
    with contextlib.suppress(OSError, ValueError):
        p = _file(kind)
        if p.is_file():
            return [json.loads(ln) for ln in p.read_text(errors="replace").splitlines() if ln.strip()]
    return []


def _stable_id(kind: str, *parts: str) -> str:
    """Deterministic id: same inputs → same row (ARTEX's dedup rule:
    inserting the same domain twice returns the same id)."""
    h = hashlib.sha1(f"{kind}|{'|'.join(str(p) for p in parts)}".encode()).hexdigest()[:12]
    return f"{kind[:2]}_{h}"


# ── assets ────────────────────────────────────────────────────────────


def insert_asset(name: str, atype: str, parent_hint: str = "") -> str:
    """Idempotent insert. parent computed from the type+name when the
    caller doesn't know it (subdomain.example.com → example.com)."""
    name = str(name or "").strip().lower().rstrip(".")
    atype = str(atype or "").strip().lower()
    aid = _stable_id("asset", atype, name)
    if any(a["id"] == aid for a in _all("asset")):
        return aid
    parent = parent_hint or _compute_parent(atype, name)
    _append(
        "asset",
        {
            "id": aid,
            "type": atype,
            "name": name,
            "parent": _stable_id("asset", _parent_type(atype), parent) if parent else "",
            "created": time.strftime("%H:%M:%S"),
        },
    )
    return aid


def _parent_type(atype: str) -> str:
    return {"subdomain": "root_domain", "endpoint": "app", "app": "subdomain", "service": "ip", "ip": ""}.get(atype, "")


def _compute_parent(atype: str, name: str) -> str:
    if atype == "subdomain" and name.count(".") >= 2:
        return ".".join(name.split(".")[-2:])
    if atype == "app" and "/" in name:
        return name.split("/")[0]
    return ""


def list_assets(atype: str = "", parent: str = "") -> list[dict]:
    out = _all("asset")
    if atype:
        out = [a for a in out if a["type"] == atype]
    if parent:
        out = [a for a in out if a.get("parent") == parent or a["name"] == parent]
    return out


def untested_assets(limit: int = 10) -> list[dict]:
    """In-scope assets with NO fact anchored to them — the honest gap
    list (ARTEX's list_untested_assets; suijin's class weights stay)."""
    anchored = {f.get("asset_id") for f in _all("fact") if f.get("asset_id")}
    return [a for a in _all("asset") if a["id"] not in anchored][:limit]


# ── facts ─────────────────────────────────────────────────────────────


def record_fact(summary: str, evidence: str = "", asset_id: str = "", observed: bool = True, confidence: float = 0.8) -> str:
    """One summary + an evidence line (the command + 1-2 lines of
    output). observed vs inferred. The anti-noise rule: one fact per
    intent, not one per curl — the CALLER enforces that by recording the
    fact when the intent concludes, not on every request."""
    fid = _stable_id("fact", summary[:200])
    if any(f["id"] == fid for f in _all("fact")):
        return fid
    _append(
        "fact",
        {
            "id": fid,
            "summary": str(summary)[:500],
            "evidence": str(evidence)[:300],
            "asset_id": asset_id,
            "observed": bool(observed),
            "confidence": round(float(confidence), 2),
            "at": time.strftime("%H:%M:%S"),
        },
    )
    return fid


def last_facts(limit: int = 8) -> list[dict]:
    return _all("fact")[-limit:]


# ── intents ───────────────────────────────────────────────────────────


INTENT_STATES = ("open", "running", "done", "exhausted", "blocked")


def add_intent(text: str, parent_fact: str = "") -> str:
    """The unit of dispatched work (ARTEX: 'a verifiable result, not a
    step' — recon/scanning/'verify the bug' are FORBIDDEN as intents;
    the planner split enforces that upstream)."""
    iid = _stable_id("intent", text[:200])
    if any(i["id"] == iid for i in _all("intent")):
        return iid
    _append(
        "intent",
        {"id": iid, "text": str(text)[:400], "state": "open", "parent_fact": parent_fact, "at": time.strftime("%H:%M:%S")},
    )
    return iid


def set_intent_state(iid: str, state: str, note: str = "") -> bool:
    """State transitions append-only (the row's history survives):
    exhausted = the budget cut it (READ the trace, continue); blocked =
    model/network failed (dispatch the same direction again); done =
    do not retry. A thin negative gets ONE recheck, never a ban."""
    if state not in INTENT_STATES:
        return False
    rows = _all("intent")
    hit = False
    for r in rows:
        if r["id"] == iid:
            r["state"] = state
            r.setdefault("history", []).append({"to": state, "note": note[:200], "at": time.strftime("%H:%M:%S")})
            hit = True
    if hit:
        _rewrite("intent", rows)
    return hit


def open_intents() -> list[dict]:
    return [i for i in _all("intent") if i["state"] in ("open", "running")]


# ── findings ──────────────────────────────────────────────────────────


FINDING_STATUSES = ("pending", "confirmed", "fixed", "false_positive", "ignored", "duplicate")


def upsert_finding(fid: str, status: str, title: str = "", exchange_ids: str = "") -> str:
    """A finding is only legal if THIS RUN triggered it. Statuses carry
    the retest lifecycle. exchange_ids bind recorded traffic (slice 3):
    'none' is honest for non-HTTP findings — never guess an id."""
    if status not in FINDING_STATUSES:
        return ""
    rows = _all("finding")
    for r in rows:
        if r["id"] == fid:
            r["status"] = status
            if exchange_ids:
                r["exchange_ids"] = exchange_ids
            _rewrite("finding", rows)
            return fid
    _append(
        "finding",
        {
            "id": fid or _stable_id("finding", title[:200], time.time()),
            "title": title[:300],
            "status": status,
            "exchange_ids": exchange_ids or "none",
            "at": time.strftime("%H:%M:%S"),
        },
    )
    return fid


def _rewrite(kind: str, rows: list[dict]) -> None:
    with contextlib.suppress(OSError):
        _file(kind).write_text("\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows) + "\n")


# ── the snapshot (what think opens with) ──────────────────────────────


def snapshot() -> str:
    """The computed state the think context STARTS with: counts, last
    facts, open intents, untested assets. The model stops re-deriving
    what it holds and reads it. Compaction cannot delete these rows."""
    facts, assets = _all("fact"), _all("asset")
    findings = [f for f in _all("finding") if f["status"] == "confirmed"]
    lines = [
        f"ROWS SNAPSHOT — {len(facts)} facts · {len(assets)} assets · "
        f"{len(open_intents())} open intents · {len(findings)} confirmed findings",
    ]
    if open_intents():
        lines.append("open intents:")
        for i in open_intents()[:5]:
            lines.append(f"  [{i['state']}] {i['text'][:100]}")
    if last_facts(4):
        lines.append("last facts:")
        for f in last_facts(4):
            tag = "obs" if f.get("observed") else "inf"
            lines.append(f"  {f['id']} ({tag}) {f['summary'][:110]}")
    ut = untested_assets(5)
    if ut:
        lines.append("untested assets: " + ", ".join(f"{a['type']}:{a['name'][:40]}" for a in ut))
    lines.append("A fact not written here does not exist next turn — record_fact when an intent concludes.")
    return "\n".join(lines)
