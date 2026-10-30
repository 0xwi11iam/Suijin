"""Secret redaction — records carry references, not literals.

Live-run evidence (2026-10-29): the model pasted Bearer
FLAG{northbridge_decoy_admin_bypass} into every call's headers — the
literal rode the decision JSON, the audit trail, and mesh broadcasts.
In a real bounty that's a captured session token leaked into every
transcript.

THE CHANNEL RULE: the REQUEST carries the real value (functionality);
the RECORDS (audit trail, context-bound result messages, mesh
broadcasts) carry `[REDACTED:credential:name]` or `[FLAG:value]`
references instead.
"""

from __future__ import annotations

import contextlib
import re

_FLAG_RE = re.compile(r"FLAG\{[^}\s]{4,}\}")


def _stored_literals() -> list[tuple[str, str]]:
    """(literal, reference) for every stored credential value."""
    out: list[tuple[str, str]] = []
    with contextlib.suppress(Exception):
        from suijin.modules.credential_store.main import _load_store

        for c in _load_store().get("credentials", []):
            val = str(c.get("value", "") or "").strip()
            name = str(c.get("service", "") or "?")
            if len(val) >= 8:
                out.append((val, f"[REDACTED:credential:{name}]"))
    return out


def redact(text: str) -> str:
    """Replace credential/flag literals with references. Never raises,
    never touches anything else. Use on EVERY record-bound copy."""
    if not text:
        return text
    out = str(text)
    for literal, ref in _stored_literals():
        if literal in out:
            out = out.replace(literal, ref)
    out = _FLAG_RE.sub("[FLAG:redacted]", out)
    return out
