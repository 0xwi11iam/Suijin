"""Output formatters — pretty JSON and status color for HUMAN surfaces.

THE CHANNEL RULE (2026-10-29): color NEVER enters the model's context —
ANSI in tool output pollutes tokens and breaks parsers. Color lives on
human surfaces only (the TUI, the CLI). rich's Console strips color
automatically when stdout is not a terminal — the capability check is
built in: interactive ⇒ colored, piped/boxed/headless ⇒ clean text.

Model-channel functions (pretty_json) return PLAIN text and are safe for
tool results; human-channel functions (status_style, stylize_http_line)
return rich style names applied only at render time.
"""

from __future__ import annotations

import contextlib
import json
import re

_STATUS_LINE = re.compile(r"^\s*(?:Status|HTTP/[\d.]+)\s*[:\s]\s*(\d{3})\b")
_exit_fail = re.compile(r"\[\w+\]\s+-1\b|\(\s*-1\b")


def pretty_json(value, indent: int = 2, cap: int = 50_000) -> str:
    """Indent JSON for readability. Accepts a parsed object or a JSON
    string; ANY failure returns the input unchanged (never breaks a
    render, never raises). PLAIN TEXT ONLY — model-channel safe."""
    try:
        obj = json.loads(value) if isinstance(value, (str, bytes)) else value
        out = json.dumps(obj, indent=indent, ensure_ascii=False, default=str)
        return out[:cap] if len(out) > cap else out
    except Exception:  # noqa: BLE001 — formatting never raises
        return value if isinstance(value, str) else str(value)


def status_style(code: int) -> str:
    """rich style name per HTTP class — HUMAN CHANNEL ONLY."""
    if 200 <= code < 300:
        return "green"
    if 300 <= code < 400:
        return "yellow"
    if code == 403:
        return "bright_red"
    if 400 <= code < 500:
        return "red"
    if 500 <= code <= 599:
        return "magenta"
    return "white"


def stylize_http_line(line: str):
    """(text, style) for a line — Status/HTTP lines get their class color,
    [COMMAND]/[EXIT]/[STDERR] prefixes get quiet tones. HUMAN CHANNEL."""
    m = _STATUS_LINE.match(line)
    if m:
        return line, status_style(int(m.group(1)))
    if line.startswith("[COMMAND]"):
        return line, "cyan"
    if line.startswith(("[EXIT]", "[STDERR]")):
        return line, "bright_red" if (_exit_fail.match(line) or "Error" in line) else "dim"
    if line.startswith(("[STDOUT]", "[sandbox]", "[shell]", "[ffuf]", "[structured]")):
        return line, "dim"
    return line, ""


def render_result_lines(text: str, printer):
    """Print a terminal-style result with per-line styling. `printer(text,
    style)` is the console's print. Degraded terminals get the same text
    with empty styles — the WORDS carry the meaning; color is emphasis."""
    for ln in str(text or "").splitlines():
        t, style = stylize_http_line(ln)
        with contextlib.suppress(Exception):
            printer(t, style=style) or None
