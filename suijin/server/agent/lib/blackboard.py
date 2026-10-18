"""Blackboard finding propagation — confirmed findings push into peers.

The ARTEX insight: their blackboard means discoveries propagate to peers
AUTOMATICALLY — no explicit broadcast needed. Suijin's mesh reads peer
digests but the agent has to choose to look. This module pushes confirmed
findings into the peer's think context via the mesh's system-announce
channel, so the next turn every peer sees what landed.

The propagation is one-directional (finder → peers) and injects as a
mesh-derived context line, not guidance — it's information, not orders.
"""

from __future__ import annotations

import contextlib


def propagate_confirmed(finding: dict, engagement_dir: str | None = None) -> None:
    """Called from _on_confirmed (exploit_catalog): push the confirmed
    finding to every live mesh peer's context. Best-effort — the mesh
    may never break cataloging."""
    with contextlib.suppress(Exception):
        from suijin.modules.agent.lib import mesh as _mesh

        eid = str(finding.get("id") or "?")
        klass = str(finding.get("class") or finding.get("vuln_class") or "?")
        sev = str(finding.get("severity") or "?")
        target = str(finding.get("target") or "?")
        title = str(finding.get("title") or "")[:100]

        # mesh system announce — every peer's gc.log gets it AND the
        # think context's MESH section renders recent system lines
        _mesh._announce_system(
            f"PEER FINDING [{sev.upper()}] {klass} on {target}: {title} ({eid}). "
            f"Do NOT re-test this — it is confirmed. Chain it or cover adjacent surface."
        )


def render_peer_findings(state: dict, limit: int = 3) -> str:
    """Render recent peer-confirmed findings for the think context.

    Reads from the mesh chat (system-attributed PEER FINDING lines).
    Distinct from mesh_broadcast: these are machine-injected, not
    model-chosen — the model didn't have to remember to share.
    """
    with contextlib.suppress(Exception):
        import os

        from suijin.modules.agent.lib import mesh as _mesh

        # read the last N lines of gc.log for PEER FINDING entries
        gc = _mesh.mesh_dir() / "gc.log"
        if not gc.is_file():
            return ""
        lines = gc.read_text(errors="ignore").splitlines()
        peer_findings = [ln for ln in lines[-40:] if "PEER FINDING" in ln and f"node-{os.getpid()}" not in ln]
        if not peer_findings:
            return ""
        out = ["## PEER FINDINGS (confirmed by teammates — do NOT re-test)", ""]
        for line in peer_findings[-limit:]:
            # strip the timestamp prefix
            text = line.split("|", 3)[-1].strip() if "|" in line else line
            out.append(f"- {text[:160]}")
        return "\n".join(out)
    return ""
