"""Tool doctrine — steer tool choice at decision time, on the result.

Live-run pattern (2026-10-30): the model uses http_request for EVERYTHING
— payload probes, fuzzing, comparisons — and never reaches the advanced
tools built for exactly that work. The catalog's "never raw curl for
evidence work" doctrine is invisible at the moment of choice.

THE MECHANISM (same as tunnel-vision + chain indicators): when the
tool_args SMELL like a job a better tool does, the result carries the
upgrade notice. Advisory — the model keeps its choice; the coaching
rides the exact observation that triggered it.
"""

from __future__ import annotations

import re

_PAYLOAD_MARKERS = re.compile(
    r"(?i)('\s*or\s*'|\bunion\s+select\b|\bsleep\s*\(|\bwaitfor\s+delay\b|\{\{.*\}\}|<script|javascript:|\.\./\.\./|\b/etc/passwd\b|\$\{jndi:|\bdocument\.cookie\b|onerror=)"
)
_FUZZ_KEYWORD = re.compile(r"FUZZ", re.I)
_COMPARE_HINT = re.compile(r"(?i)\bcompare\b|\bbaseline\b|\bcontrol_")
_JSON_BODY_HINT = re.compile(r"(?i)content-type.*json|^\s*\{.*\}\s*$")


def upgrade_notice(tool_name: str, tool_args: dict) -> str:
    """One coaching line when a better tool owns this exact job."""
    try:
        args = tool_args if isinstance(tool_args, dict) else {}
        body = str(args.get("body") or "")
        url = str(args.get("url") or "")
        headers = str(args.get("headers") or "")

        if tool_name != "http_request":
            return ""

        if _FUZZ_KEYWORD.search(url):
            return (
                "[TOOL UPGRADE] FUZZ keyword in a raw http_request — ffuf_fuzz owns content "
                "discovery: parallel, filtered (-fc 404), and its -of json output arrives as a "
                "clean results table. One ffuf_fuzz call replaces dozens of these."
            )
        if _PAYLOAD_MARKERS.search(body) or _PAYLOAD_MARKERS.search(str(args.get("payload") or "")):
            if _COMPARE_HINT.search(body + headers):
                return (
                    "[TOOL UPGRADE] hand-rolling a compare/baseline via raw http_request — "
                    "http_replay(compare=true) IS the 3-gate diff (baseline vs exploit, one "
                    "call, verdict included). Raw comparisons are how false positives get recorded."
                )
            return (
                "[TOOL UPGRADE] injection-shaped payload via raw http_request — inject_probe "
                "runs the full class battery (tag survival, sink context, error fingerprints, "
                "boolean pairs vs a measured noise floor) before you craft by hand. Probe "
                "BATTERIES first; hand payloads only for the novel cases they miss."
            )
        if _JSON_BODY_HINT.match(body) and any(k in url.lower() for k in ("/api/", "/v1/", "graphql")):
            return (
                "[TOOL UPGRADE] API work via raw http_request — http_replay carries payloads "
                "as DATA through 15 mutation ops + 12 codecs and keeps the credential swap "
                "one argument away. Raw calls are fine for recon fetches."
            )
        return ""
    except Exception:  # noqa: BLE001 — doctrine never breaks a tool call
        return ""
