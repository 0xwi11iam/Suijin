"""auth_diff — differential authorization testing, ONE tool call.

The operator's method, encoded: take a request that references an
object, replay it under TWO sessions with substituted object ids, and
compare. If session B sees session A's object, that's the bug class —
reported factually, no interpretation theater.

Builds on the credential bridge: apply_credential attaches each named
session's jar; http request/response captured cleanly for the diff.

Honesty rules:
  - verdicts describe OBSERVATIONS (same/different), never conclusions
    ("idor confirmed") — the human decides with the evidence shown
  - access_by_b requires BOTH requests to succeed for a meaningful diff
  - server-side noise (timestamps, nonces) is noted via a similarity
    score, not hidden
"""

from __future__ import annotations

import difflib
import hashlib
import json

from suijin.server.tools.lib.http_replay import apply_credential


def _send(url: str, method: str, headers: dict, body: str = "", timeout: int = 20) -> dict:
    import requests

    try:
        r = requests.request(method, url, headers=headers, data=body or None,
                             timeout=timeout, allow_redirects=False)
        return {
            "status": r.status_code,
            "length": len(r.content or b""),
            "body_hash": hashlib.md5(r.content or b"").hexdigest()[:12],
            "body": (r.text or "")[:1500],
            "content_type": r.headers.get("content-type", ""),
        }
    except Exception as e:  # noqa: BLE001
        return {"status": -1, "error": str(e)[:120]}


def auth_diff(
    url_template: str = "",
    object_refs: str = "",
    session_a: str = "",
    session_b: str = "",
    method: str = "GET",
    body_template: str = "",
    extra_headers: str = "",
    config: dict | None = None,
) -> str:
    """Differential authorization probe.

    url_template: URL containing {ID} — e.g.
        https://host/api/images/{ID}
    object_refs: comma-separated ids to try (the operator's 'image ids')
    session_a / session_b: named credentials (suijin auth sessions) —
        two accounts; the interesting question is what B sees of A's
        objects. With one session, same-session swaps still show
        whether the server checks ownership at all.
    """
    template = str(url_template or "").strip()
    if not template or "{ID}" not in template:
        return (
            "Error: url_template must contain {ID} — e.g. "
            "'https://host/api/images/{ID}'. object_refs: comma-separated ids."
        )
    refs = [x.strip() for x in str(object_refs or "").split(",") if x.strip()]
    if not refs:
        return "Error: object_refs required (comma-separated ids to substitute into {ID})"

    try:
        extra = json.loads(extra_headers) if extra_headers else {}
    except Exception:  # noqa: BLE001
        extra = {}

    lines = [f"auth_diff — {template} · sessions: {session_a or 'anon'} vs {session_b or 'anon'}", ""]
    results = []
    for ref in refs:
        url = template.replace("{ID}", ref)
        row = {"ref": ref}
        for label, cred in (("a", session_a), ("b", session_b)):
            req = apply_credential(
                {"method": method.upper(), "url": url, "headers": dict(extra)},
                cred or None,
            )
            row[label] = _send(req["url"], req["method"], req.get("headers", {}), body_template)
        a, b = row.get("a", {}), row.get("b", {})
        if a.get("status") == -1 or b.get("status") == -1:
            lines.append(f"  {ref}: transport error (a={a.get('error','')[:40]} b={b.get('error','')[:40]})")
            results.append(row)
            continue
        same_status = a.get("status") == b.get("status")
        same_body = a.get("body_hash") == b.get("body_hash")
        sim = difflib.SequenceMatcher(None, a.get("body", ""), b.get("body", "")).ratio()
        interesting = same_status and 200 <= int(a.get("status", 0)) < 300 and not same_body is False and sim > 0.97
        verdict = (
            "BOTH-200-NEAR-IDENTICAL (session B sees the same object payload)"
            if interesting
            else f"different (a={a.get('status')} {a.get('length')}b vs b={b.get('status')} {b.get('length')}b, similarity {sim:.2f})"
        )
        row["verdict"] = verdict
        row["similarity"] = round(sim, 2)
        results.append(row)
        lines.append(f"  {ref}: a={a.get('status')}({a.get('length')}b) b={b.get('status')}({b.get('length')}b) sim={sim:.2f} → {verdict}")

    # the summary says what a human needs to decide, nothing more
    hits = [r for r in results if str(r.get("verdict", "")).startswith("BOTH-200")]
    lines.append("")
    if hits:
        lines.append(
            f"OBSERVATION: {len(hits)}/{len(results)} object refs returned near-identical "
            "payloads to BOTH sessions. If those objects belong to another principal, "
            "this is the classic cross-tenant read. Verify ownership manually before "
            "reporting — same-200 can also mean public data."
        )
    else:
        lines.append("OBSERVATION: no ref returned matching payloads to both sessions "
                     "in this batch — either authorization held, or the objects/params need another look.")
    lines.append("evidence: pair each ref's full requests/responses (captured above as status/length/hash); "
                 "re-run with body capture for the submission packet.")
    return "\n".join(lines)
