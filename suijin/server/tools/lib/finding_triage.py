"""Finding triage — the dedup lifecycle (2026-10-20, ARTEX finding_cases).

Bug bounty programs penalize duplicate reports; our own trails showed
title-drift minting near-dupe entries (the same sink re-cataloged under
a rephrased title). The ARTEX doctrine, adapted:

- ROOT CAUSE decides, never similarity alone: same target + same class
  + overlapping endpoint/marker/POC = duplicate. Different endpoint or
  provably different mechanism = distinct. Anything else = uncertain →
  flagged for the operator, NEVER auto-merged.
- severity_reason is mandatory on merged/duplicate records: based on
  verified impact, never "take the highest member".
- Lifecycle: new → duplicate_of:<EXP-id> | approved (operator mark).
"""

from __future__ import annotations

import contextlib
import re


def _host_of(target: str) -> str:
    m = re.search(r"https?://([^/\s]+)", str(target or ""))
    return (m.group(1).lower() if m else str(target or "").lower()).split(":")[0]


def _path_of(target: str) -> str:
    m = re.search(r"https?://[^/\s]+(/[^\s?]*)", str(target or ""))
    return (m.group(1) or "/").rstrip("/").lower() or "/"


def _poc_set(entry: dict) -> set[str]:
    out = set()
    for s in entry.get("poc") or []:
        if isinstance(s, dict):
            cmd = str(s.get("cmd") or "")
            # normalize the volatile parts: ids, values
            cmd = re.sub(r"[0-9a-f]{8,}", "<id>", cmd)
            cmd = re.sub(r"\d+", "<n>", cmd)
            if cmd:
                out.add(cmd.strip()[:120])
    return out


def root_cause_compare(a: dict, b: dict) -> str:
    """merge | distinct | uncertain — root-cause doctrine."""
    try:
        if _host_of(a.get("target")) != _host_of(b.get("target")):
            return "distinct"  # different assets, never a dupe
        if str(a.get("class")) != str(b.get("class")):
            # same asset, different vulnerability class — distinct ROOT
            # unless the class pair is an alias pair the normalizer splits
            return "distinct"
        pa, pb = _path_of(a.get("target")), _path_of(b.get("target"))
        same_path = pa == pb or pa.rstrip("/<id>") == pb.rstrip("/<id>")
        pa_overlap = bool(pa and pb and (pa in pb or pb in pa))
        poc_overlap = bool(_poc_set(a) & _poc_set(b))
        marker_same = str(a.get("marker") or "") and str(a.get("marker") or "") == str(b.get("marker") or "")
        if same_path and (poc_overlap or marker_same or not _poc_set(b)):
            return "merge"
        if pa_overlap and poc_overlap:
            return "merge"
        if same_path and not _poc_set(a) and not _poc_set(b):
            return "uncertain"  # same sink, no POCs to compare — operator calls it
        return "distinct"
    except Exception:  # noqa: BLE001 — triage never breaks cataloging
        return "uncertain"


def scan(entries: dict) -> list[dict]:
    """Find duplicates across a catalog's entries (dict of eid → entry).
    Returns a list of {duplicate, primary, verdict, reason} — merge
    verdicts only when confident; uncertain stays operator business."""
    out: list[dict] = []
    eids = sorted(entries.keys())
    for i, dup_id in enumerate(eids):
        a = entries[dup_id]
        if not isinstance(a, dict) or a.get("triage", {}).get("status") == "approved":
            continue
        for prim_id in eids[:i]:
            b = entries[prim_id]
            if not isinstance(b, dict) or b.get("triage", {}).get("status") == "approved":
                continue
            v = root_cause_compare(a, b)
            if v in ("merge", "uncertain"):
                out.append(
                    {
                        "duplicate": dup_id,
                        "primary": prim_id,
                        "verdict": v,
                        "reason": (
                            f"same host+class, path {_path_of(a.get('target'))!r} vs {_path_of(b.get('target'))!r}"
                        ),
                    }
                )
                break  # first primary wins; scan again after operator marks
    return out


def check_new_entry(entry: dict, entries: dict) -> dict | None:
    """At catalog-write time: the triage state for a NEW entry against
    the existing catalog. Returns the triage dict (or None when clean
    new). Merge verdicts auto-mark duplicate_of — uncertain marks flag
    for the operator but stay reported."""
    try:
        cands = []
        for eid, other in entries.items():
            if not isinstance(other, dict) or eid == entry.get("id"):
                continue
            v = root_cause_compare(entry, other)
            if v == "merge":
                cands.append((eid, v))
            elif v == "uncertain":
                cands.append((eid, v))
        if not cands:
            return {"status": "new"}
        prim, verdict = cands[0]
        if verdict == "merge":
            return {
                "status": "duplicate_of",
                "of": prim,
                "reason": "root-cause match: same host+class+sink (path/marker/POC overlap)",
            }
        return {"status": "flagged", "of": prim, "reason": "possible duplicate — operator to confirm (uncertain root-cause)"}
    except Exception:  # noqa: BLE001
        return None


def render_for_report(entries: dict) -> str:
    """The report block: primary findings vs duplicates vs flagged.
    Duplicates NEVER disappear silently — they render under their
    primary with the reason."""
    try:
        dups = scan(entries)
        if not dups:
            return ""
        lines = ["### Duplicate findings (triaged)"]
        for d in dups:
            tag = "DUPLICATE" if d["verdict"] == "merge" else "POSSIBLE (needs operator confirm)"
            lines.append(f"- {d['duplicate']} → {tag} of {d['primary']} — {d['reason']}")
        lines.append("Duplicates are excluded from the submission count; confirm or clear via triage status.")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return ""


def mark(entries: dict, eid: str, status: str, of: str = "") -> str:
    """Operator/model mark: approved | duplicate_of | new."""
    with contextlib.suppress(Exception):
        e = entries.get(eid)
        if not isinstance(e, dict):
            return f"Error: no entry {eid}"
        if status == "approved":
            e["triage"] = {"status": "approved"}
        elif status == "duplicate_of" and of in entries:
            e["triage"] = {"status": "duplicate_of", "of": of, "reason": "operator-marked"}
        elif status == "new":
            e["triage"] = {"status": "new"}
        else:
            return "Error: status must be approved | duplicate_of (with of=) | new"
        return f"triage: {eid} → {e['triage']['status']}" + (f" (of {of})" if of else "")
    return "Error: triage mark failed"
