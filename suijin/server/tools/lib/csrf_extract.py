"""csrf_extract (2026-10-20, CyberStrike adaptation).

Stateful endpoints need a FRESH CSRF token per request — replaying a
captured request with a stale token fails, and the model then guesses
at refresh mechanics. This tool fetches the token-bearing page (with
an optional credential), extracts the token from the usual places —
hidden form inputs, meta tags, response headers, cookies — and returns
the READY-MADE http_replay mutation that injects it.
"""

from __future__ import annotations

import re

from suijin.modules.tools.lib.http_tools import http_request as _hr_http_request

_TOKEN_INPUT_RX = re.compile(
    r"<input[^>]+name=[\"']([A-Za-z0-9_\-\.]*(?:csrf|token|authenticity)[A-Za-z0-9_\-\.]*)[\"'][^>]*"
    r"value=[\"']([^\"']+)[\"']",
    re.I,
)
_TOKEN_INPUT_RX_REV = re.compile(
    r"<input[^>]+value=[\"']([^\"']+)[\"'][^>]*name=[\"']([A-Za-z0-9_\-\.]*(?:csrf|token|authenticity)[A-Za-z0-9_\-\.]*)[\"']",
    re.I,
)
_META_RX = re.compile(
    r"<meta[^>]+name=[\"']([A-Za-z0-9_\-]*(?:csrf|token)[A-Za-z0-9_\-]*)[\"'][^>]+content=[\"']([^\"']+)[\"']",
    re.I,
)


def csrf_extract(url: str, credential: str = "", config: dict | None = None) -> str:
    """Fetch a token-bearing page and return the fresh CSRF token as a
    ready http_replay mutation. Checks: hidden inputs, meta tags,
    X-CSRF-* headers, csrf-named cookies. (credential reserved — the
    token page is usually pre-auth.)"""
    try:
        target = str(url or "").strip()
        if not target:
            return "Error: url required"
        # send through the SAME governed engine the model uses (pacing,
        # observation) — plain GET; the token page is usually pre-auth
        out = str(_hr_http_request("GET", target, None, "") or "")
        if out.startswith("Error"):
            return f"fetch failed: {out[:300]}"

        found: list[tuple[str, str, str]] = []  # (name, value, source)
        for name, value in _TOKEN_INPUT_RX.findall(out):
            found.append((name, value, "form input"))
        for value, name in _TOKEN_INPUT_RX_REV.findall(out):
            found.append((name, value, "form input"))
        for name, content in _META_RX.findall(out):
            found.append((name, content, "meta tag"))

        # header + cookie extraction from the formatted output
        header_m = re.search(r"(?im)^\s*(X-[A-Za-z\-]*(?:CSRF|Token)[A-Za-z\-]*):\s*(\S+)", out)
        if header_m:
            found.append((header_m.group(1), header_m.group(2), "response header"))
        cookie_m = re.search(
            r"(?im)(?:set-cookie:?\s*)?([A-Za-z0-9_\-]*(?:csrf|xsrf|token)[A-Za-z0-9_\-]*)=([A-Za-z0-9_\-\.=/+]{16,})",
            out,
        )
        if cookie_m:
            found.append((cookie_m.group(1), cookie_m.group(2), "cookie"))

        # dedupe by (name, value)
        seen = set()
        uniq = []
        for name, value, src in found:
            k = (name.lower(), value)
            if k not in seen:
                seen.add(k)
                uniq.append((name, value, src))

        if not uniq:
            return (
                "No CSRF token found on that page. If the flow needs one, check: a different "
                "endpoint serves it, it rides a prior redirect, or the app uses double-submit "
                "cookies (compare cookie value vs a header you must echo)."
            )

        lines = [f"CSRF token(s) extracted from {target}:"]
        for name, value, src in uniq[:3]:
            lines.append(f"- {name} ({src}): {value[:80]}{'…' if len(value) > 80 else ''}")
        name, value, src = uniq[0]
        lines.append("")
        lines.append("READY http_replay mutation (copy into ops):")
        if src == "cookie":
            lines.append(f'  {{"op": "set-cookie", "name": "{name}", "value": "{value}"}}')
        elif src == "response header":
            lines.append(f'  {{"op": "set-header", "name": "{name}", "value": "{value}"}}')
        else:
            lines.append(
                f'  {{"op": "set-body-field", "name": "{name}", "value": "{value}"}}  '
                f'(or set-header "{name}" for header-token APIs)'
            )
        lines.append("Fetch it FRESH per stateful request — tokens are usually single-use.")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"
