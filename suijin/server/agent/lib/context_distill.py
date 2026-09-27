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

#: credential shapes that must NEVER be lost to distillation — a secret
#: living deep in a long response is exactly the thing the agent must not
#: forget (the bench's {{TOKEN}} flow was the canary: the token rode past
#: the 8k head and the scripted chain went blind)
_SECRET_RES = [
    re.compile(r"access_token[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9._\-]{16,})"),
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b"),
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,})\b"),
    re.compile(r"\b(eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,})\b"),
    re.compile(r"\b([A-Fa-f0-9]{32})\b"),
]


def extract_secret_excerpts(output: str, max_hits: int = 4) -> list[str]:
    """Credential-shaped strings pulled from the FULL output, verbatim.

    These ride below the distillate so a key 20k chars into a response
    stays in context — distillation must shrink noise, never weapons."""
    hits: list[str] = []
    seen: set[str] = set()
    for rx in _SECRET_RES:
        for m in rx.finditer(str(output or "")):
            v = m.group(1)
            if v not in seen:
                seen.add(v)
                hits.append(v)
                if len(hits) >= max_hits:
                    return hits
    return hits


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
