"""Contamination scrub — .sje resume hygiene.

The field bug (2026-10-18): a resumed engagement carried leaked API keys
from the PREVIOUS run's messages. The keys were verified DEAD (SERVICE_
DISABLED) but the verdict didn't ride with the finding text — the model
saw them as fresh discoveries and burned iterations re-testing dead
keys.

This module scrubs .sje state on resume: findings/messages that
reference known-dead indicators get their literal key text replaced
with a verdict marker. The model sees "[VERDICT: dead — see memory]"
instead of the actual key.
"""

from __future__ import annotations

import re

#: patterns that indicate a dead/false-positive finding in message text
_DEAD_MARKERS = [
    r"SERVICE_DISABLED",
    r"API_KEY_SERVICE_BLOCKED",
    r"verified dead",
    r"false.?positive",
    r"does not reproduce",
    r"not exploitable",
    r"by-design",
    r"REJECTED",
]

#: credential patterns to scrub from carried messages
_CRED_PATTERNS = [
    (re.compile(r"AIza[0-9A-Za-z_-]{30,40}"), "AIzaSy[SCRUBBED:dead-key]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA[SCRUBBED:dead-key]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "gh_[SCRUBBED:dead-key]"),
    (re.compile(r"xox[abpr]-[A-Za-z0-9-]{10,}"), "xox-[SCRUBBED:dead-key]"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "sk-[SCRUBBED:dead-key]"),
]

_DEAD_RX = re.compile("|".join(_DEAD_MARKERS), re.I)


def scrub_state(state: dict) -> dict:
    """Scrub dead credentials from .sje state before resume. Returns the
    cleaned state (mutates in place — the .sje is a copy anyway)."""
    msgs = state.get("messages") or []
    for msg in msgs:
        content = str(msg.get("content") or "")
        if not content:
            continue
        # check if this message references a dead finding
        if _DEAD_RX.search(content):
            # scrub credential literals from the message
            scrubbed = content
            for rx, replacement in _CRED_PATTERNS:
                scrubbed = rx.sub(replacement, scrubbed)
            if scrubbed != content:
                msg["content"] = scrubbed

    # also scrub from execution_trace
    trace = state.get("execution_trace") or []
    for step in trace:
        output = str(step.get("tool_output") or "")
        if _DEAD_RX.search(output):
            scrubbed = output
            for rx, replacement in _CRED_PATTERNS:
                scrubbed = rx.sub(replacement, scrubbed)
            if scrubbed != output:
                step["tool_output"] = scrubbed

    return state
