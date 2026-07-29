"""Diagnostic classification of tool-step outcomes.

Distinguishes shell-quoting glitches from real 4xx from 5xx-in-3ms
parse-time crashes. The LLM sees WHICH kind of failure happened, not
just THAT one did.

Ported from redamon/agentic/orchestrator_helpers/error_class.py.
"""
from __future__ import annotations

import re
from typing import Optional

_SHELL_PARSER_PATTERNS = [
    re.compile(r"\bno closing quot", re.IGNORECASE),
    re.compile(r"unexpected end of file", re.IGNORECASE),
    re.compile(r"syntax error near unexpected token", re.IGNORECASE),
    re.compile(r"\bshlex\.", re.IGNORECASE),
    re.compile(r"ValueError:\s*No closing", re.IGNORECASE),
    re.compile(r"bash: line \d+: syntax error", re.IGNORECASE),
]

_TRANSPORT_PATTERNS = [
    re.compile(r"could not resolve host", re.IGNORECASE),
    re.compile(r"connection refused", re.IGNORECASE),
    re.compile(r"connection timed out", re.IGNORECASE),
    re.compile(r"name or service not known", re.IGNORECASE),
    re.compile(r"network is unreachable", re.IGNORECASE),
    re.compile(r"no route to host", re.IGNORECASE),
    re.compile(r"ssl(?:v\d)?\s+handshake", re.IGNORECASE),
    re.compile(r"NewConnectionError", re.IGNORECASE),
    re.compile(r"ConnectTimeoutError", re.IGNORECASE),
    re.compile(r"\bENETUNREACH\b"),
    re.compile(r"\bEHOSTUNREACH\b"),
]

_TOOL_INTERNAL_PATTERNS = [
    re.compile(r"\[ERROR\]\s*execute_\w+\s+failed:\s*returncode=", re.IGNORECASE),
    re.compile(r"option\s+-\w+:\s*error encountered when reading a file", re.IGNORECASE),
    re.compile(r"file not found\b", re.IGNORECASE),
    re.compile(r"no such file or directory", re.IGNORECASE),
    re.compile(r"Tool execution failed:", re.IGNORECASE),
    re.compile(r"Command timed out after", re.IGNORECASE),
    re.compile(r"command not found", re.IGNORECASE),
]

_HTTP_STATUS_PATTERNS = [
    re.compile(r"HTTP/[0-9.]+\s+(\d{3})\b"),
    re.compile(r"^\s*Status[:\s]+(\d{3})\b", re.MULTILINE),
    re.compile(r"\bStatus(?:Code)?[:=]\s*(\d{3})\b"),
]

_GENERIC_5XX_BODY_MARKERS = (
    "internal server error", "service unavailable",
    "bad gateway", "gateway timeout",
)
_GENERIC_4XX_BODY_MARKERS = (
    "method not allowed", "not found", "unauthorized",
    "forbidden", "bad request",
)

FAST_RESPONSE_THRESHOLD_MS = 50
NETWORKED_FAST_THRESHOLD_MS = 200


def classify_error_class(
    *,
    success: bool,
    tool_output: Optional[str],
    error_message: Optional[str],
    duration_ms: Optional[int],
    tool_name: Optional[str] = None,
) -> str:
    """Classify a tool step's outcome into a diagnostic error class."""
    if success:
        return "success"

    combined = ((tool_output or "") + " " + (error_message or ""))[:6000]

    # 1. Shell/parser errors
    for pat in _SHELL_PARSER_PATTERNS:
        if pat.search(combined):
            return "shell_parser_error"

    # 2. Transport errors
    for pat in _TRANSPORT_PATTERNS:
        if pat.search(combined):
            return "transport_error"

    # 3. Tool internal errors
    for pat in _TOOL_INTERNAL_PATTERNS:
        if pat.search(combined):
            return "tool_internal_error"

    # 4. Extract HTTP status
    http_status = None
    for pat in _HTTP_STATUS_PATTERNS:
        m = pat.search(combined)
        if m:
            http_status = int(m.group(1))
            break

    if http_status is not None:
        if 400 <= http_status < 500:
            return "application_4xx"
        if http_status >= 500:
            dur = duration_ms or 0
            if dur < FAST_RESPONSE_THRESHOLD_MS:
                return "application_5xx_fast"
            if dur < NETWORKED_FAST_THRESHOLD_MS:
                return "application_5xx_networked_fast"
            return "application_5xx_normal"

    # 5. Generic body markers
    combined_lower = combined.lower()
    if any(m in combined_lower for m in _GENERIC_5XX_BODY_MARKERS):
        dur = duration_ms or 0
        if dur < FAST_RESPONSE_THRESHOLD_MS:
            return "application_5xx_fast"
        return "application_5xx_normal"
    if any(m in combined_lower for m in _GENERIC_4XX_BODY_MARKERS):
        return "application_4xx"

    return "tool_internal_error"


def is_diagnostic_failure(error_class: str) -> bool:
    """True if the error class indicates a non-semantic failure — the probe
    never reached the application's business logic, so the LLM should NOT
    mark this vector as 'tested'."""
    return error_class in (
        "shell_parser_error", "transport_error", "tool_internal_error",
    )
