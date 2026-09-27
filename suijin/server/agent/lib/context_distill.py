"""Write-time result distillation — the context-cost lever (R1).

Tool results enter the conversation state as RESULT messages. Raw, a
single big HTTP response is 35KB of context that rides EVERY turn's embed
until compaction finally eats it. The full copy already lives, untouched,
in the audit trail and toollogs — the state copy only needs what the
model can act on: the status/head, the interesting headers, and enough
body to reason with. Distill at WRITE time; keep the truth where it
already is.
"""

from __future__ import annotations

import re

#: default per-result cap in state (config: context_result_cap)
DEFAULT_RESULT_CAP = 8_000

_POINTER = "\n…[distilled {kept} of {total} chars — full output lives in the audit trail / toollogs]"

#: header lines worth keeping verbatim in the distillate (evidence value)
_KEEP_HEADER_RE = re.compile(
    r"^(status|server|content-type|location|set-cookie|x-|www-authenticate|retry-after"
    r"|verdict|confir|exploit|flag|cred)",
    re.IGNORECASE,
)


def distill_result(output: str, cap: int | None = None) -> str:
    """The state-bound copy of a tool result: head + key headers + body
    head, capped, with an honest pointer to the full copy.

    Never raises; never returns empty for non-empty input (a result the
    model cannot see at all is worse than a truncated one)."""
    try:
        text = str(output or "")
        if not text:
            return ""
        limit = int(cap or DEFAULT_RESULT_CAP)
        if len(text) <= limit:
            return text
        lines = text.splitlines()
        # keep the head block + interesting headers, then as much body as fits
        kept: list[str] = []
        used = 0
        in_head = True
        for ln in lines:
            cost = len(ln) + 1
            if in_head and (not ln.strip() or _KEEP_HEADER_RE.match(ln)):
                if used + cost > limit // 2:
                    in_head = False
                else:
                    kept.append(ln)
                    used += cost
                    continue
            in_head = False
            if used + cost > limit - 160:  # room for the pointer
                break
            kept.append(ln)
            used += cost
        distilled = "\n".join(kept).rstrip()
        if not distilled:
            distilled = text[: limit - 200]
        return distilled + _POINTER.format(kept=len(distilled), total=len(text))
    except Exception:  # noqa: BLE001 — distillation must never break a result
        return str(output or "")
