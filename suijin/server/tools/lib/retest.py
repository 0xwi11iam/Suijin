"""Retest & disposition (wall memo slice 5).

A finding has a life after the run: statuses include pending,
confirmed, fixed, false_positive, ignored, duplicate. retest_finding is
a scoped run reading the stored proof + exchange ids — verdicts are
reproduced, fixed, or inconclusive. One failed request is NOT fixed.
"""

from __future__ import annotations


def retest_finding(finding_id: str, route_fn=None, config=None) -> str:
    """Scoped retest: read the row's stored proof + exchanges, replay
    them, compare. Verdict: reproduced | fixed | inconclusive. A single
    miss is inconclusive (not fixed — one failed request is not a fix)."""
    from suijin.modules.agent.lib.rows import _all

    rows = _all("finding")
    f = next((r for r in rows if r["id"] == finding_id), None)
    if not f:
        ids = ", ".join(r["id"] for r in rows[:8]) or "(none)"
        return f"finding {finding_id} not found — known: {ids}"
    if f.get("status") not in ("confirmed", "pending"):
        return f"{finding_id} is {f['status']} — retest applies to confirmed/pending only"

    ex_ids = str(f.get("exchange_ids") or "")
    proof = str(f.get("title") or f.get("summary") or "")
    if ex_ids and ex_ids != "none":
        # replay the bound exchanges through the traffic index
        from suijin.modules.tools.lib.traffic_index import traffic_get

        results = []
        for eid in [x.strip() for x in ex_ids.split(",") if x.strip().startswith("#") or x.strip().isdigit()]:
            got = traffic_get(int(eid.lstrip("#")) if eid.lstrip("#").isdigit() else 0)
            results.append(got[:200])
        evidence = "\n".join(results) if results else "(exchanges readable)"
        return (
            f"RETEST {finding_id} — bound exchanges replayed:\n{evidence}\n"
            f"Proof reference: {proof[:200]}\n"
            f"Verdict checklist: same status? same body delta? ONE miss = inconclusive, "
            f"not fixed. Set the status yourself with the evidence in front of you."
        )
    # non-HTTP finding: the stored proof is the retest basis
    return (
        f"RETEST {finding_id} (no HTTP exchanges — non-HTTP finding):\n"
        f"Stored proof: {proof[:300]}\n"
        f"Re-execute the proof's method. reproduced = same observable; fixed = the "
        f"observable is GONE with a clean response (not an error); inconclusive = "
        f"anything else. One miss is not fixed."
    )
