"""report_finding — the tool that was missing the night 72 calls died.

`record_finding` (intel) records DEFENSIVE intel (waf, rate_limit,
blocks…) into the knowledge graph — its enum was the wall: workers
finding a real vuln had nowhere to put it (xss/idor/open_redirect
were 'invalid finding type') and findings died in rejection loops.

This module is the vulnerability path:
  report_finding(target, title, severity, vuln_type, evidence, url)
    → writes the swarm findings table (the honest board `swarm status`
      reads) + a matching fact for the report generator

Honesty rule baked into the return: status is 'reported', NOT
'verified' — verification is a CONTROL TEST (a negative case), and the
catalog CONFIRMED lesson (2026-11-06) is quoted at the agent so the
distinction rides every confirmation message.
"""

from __future__ import annotations

import contextlib
import os
import re
import time

VULN_TYPES = (
    "xss", "idor", "open_redirect", "ssrf", "auth_bypass", "sqli",
    "rce", "info_disclosure", "csrf", "clickjacking", "logic", "race",
    "upload_bypass", "deserialization", "jwt", "cors", "other",
)
SEVERITIES = ("critical", "high", "medium", "low", "info")


def report_vuln_finding(
    target: str = "",
    title: str = "",
    severity: str = "",
    vuln_type: str = "",
    evidence: str = "",
    url: str = "",
    config: dict | None = None,
) -> str:
    """Record a vulnerability finding on the swarm's honest board."""
    target = str(target or "").strip()
    title = str(title or "").strip()
    severity = str(severity or "low").strip().lower()
    vuln_type = str(vuln_type or "other").strip().lower().replace("-", "_").replace(" ", "_")

    if not target or not title:
        return (
            "Error: target and title required. "
            "report_finding(target='host', title='what is broken', severity='high', "
            "vuln_type='idor', evidence='request/response proof', url='the vulnerable endpoint')"
        )
    # TARGET-IDENTITY GATE (2026-11-16, the false-positive lesson): a
    # finding against localhost/private space is a TEST result, never a
    # submittable finding — refuse it unless the engagement explicitly
    # opted into local-target testing (guardrail_allow_local_target).
    import os as _os
    import re as _re

    _t = str(target or "")
    _is_local = _re.match(r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|localhost\[?)", _t, _re.I) or "localhost" in _t.lower()
    if _is_local and not (_os.environ.get("SUIJIN_ALLOW_LOCAL_TARGET") or "").lower() in ("1", "true", "yes"):
        return (
            "REFUSED: this looks like a LOCAL/PRIVATE target (" + _t[:40] + "). "
            "Local targets are test fixtures, not reportable findings — a "
            "CONFIRMED result against one is a false positive by definition "
            "(the 2026-11-15 overnight sweep burned a night on exactly this). "
            "Set SUIJIN_ALLOW_LOCAL_TARGET=1 only for dedicated local-test "
            "engagements, and never submit these findings anywhere."
        )

    if severity not in SEVERITIES:
        return f"Error: severity must be one of {', '.join(SEVERITIES)}"
    if vuln_type not in VULN_TYPES:
        return f"Error: vuln_type must be one of {', '.join(sorted(set(VULN_TYPES)))}"

    evidence = str(evidence or "")[:4000]
    url = str(url or "")[:500]
    fid = f"find-{int(time.time()) % 100000}-{os.urandom(3).hex()}"
    agent = os.environ.get("SUIJIN_IDENTITY", "")
    now = time.time()
    wrote = False

    with contextlib.suppress(Exception):
        import sqlite3

        from suijin.server.swarm.coordinator import swarm_db_path

        db = sqlite3.connect(str(swarm_db_path()), timeout=10)
        db.execute(
            "INSERT INTO findings (id, title, status, severity, agent_id, engagement, created_at) "
            "VALUES (?, ?, 'reported', ?, ?, ?, ?)",
            (fid, f"[{vuln_type}] {title}"[:1000], severity, agent, target, now),
        )
        db.execute(
            "INSERT INTO facts (id, summary, evidence, agent_id, engagement, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"fact-{os.urandom(3).hex()}",
             f"REPORTED {severity} {vuln_type}: {title} ({url or 'no url'})"[:300],
             evidence[:200], agent, target, now),
        )
        db.commit()
        db.close()
        wrote = True

    # THE BUS: findings flow to every node (host, boxed, remote) — the
    # centralized-data directive. Live peers + any subscribed client hear it.
    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import BusClient
        from suijin.server.swarm.lifecycle import _bus_addr

        addr = _bus_addr()
        if addr:
            bc = BusClient()
            if bc.connect(addr[0], addr[1], f"finder-{os.urandom(2).hex()}"):
                bc.publish(f"/engagement/{target}/findings",
                           {"severity": severity, "vuln_type": vuln_type,
                            "title": title[:200], "url": url, "agent": agent})
                bc.close()

    if not wrote:
        # no swarm db (single-agent runs) — the fact store still wants it
        with contextlib.suppress(Exception):
            from suijin.server.agent.lib.rows import add_fact

            add_fact({"summary": f"REPORTED {severity} {vuln_type}: {title}", "evidence": evidence[:200]})

    note = (
        f"FINDING RECORDED [{fid}] {severity}/{vuln_type} on {target}: {title}\n"
        f"evidence: {evidence[:200] or '(none provided — add it before submitting)'}\n"
        "status is REPORTED, not verified. Verification = a CONTROL TEST "
        "(prove the negative case — e.g. swap the object id back, check the "
        "filter rejects your payload). The catalog's CONFIRMED once stamped "
        "a false positive 'unsanitized' because reproduction ≠ verification."
    )
    return note
