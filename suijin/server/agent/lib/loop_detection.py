"""Layered loop detection — pure, deterministic, independently testable.

The CyberStrike insight: their stuck/ package has three pure detectors
(repeat, signals, stuck) that catch loops the single-counter approach
misses. Suijin's `_no_progress_turns` counter miscounts gate refusals
as no-progress and misses cross-turn identical-call loops entirely.

Three layers (all pure functions — no state mutation, no I/O):
- Layer 1: within-turn duplicate suppressor (already in dispatch)
- Layer 2: cross-turn repeat detector (same tool+args across turns)
- Layer 3: meaningful-progress detector (findings/surfaces/creds = progress)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass
class RepeatVerdict:
    level: str  # "ok" | "nudge" | "abort"
    signature: str = ""
    count: int = 0
    message: str = ""


class RepeatDetector:
    """Cross-turn identical-call detector.

    Accumulates (tool, args) signatures over the whole run. Reacts when
    any signature repeats past a limit. Pure + deterministic — feed it
    signatures, get a verdict.
    """

    def __init__(self, limit: int = 3):
        self.limit = limit
        self._counts: dict[str, int] = {}
        self._nudged: set[str] = set()

    def feed(self, tool: str, args: dict) -> RepeatVerdict:
        """Feed one tool call's signature. Returns the strongest verdict."""
        sig = _sig(tool, args)
        self._counts[sig] = self._counts.get(sig, 0) + 1
        count = self._counts[sig]

        if count < self.limit:
            return RepeatVerdict("ok", sig, count)

        if sig not in self._nudged:
            self._nudged.add(sig)
            return RepeatVerdict(
                "nudge",
                sig,
                count,
                f"You've called {_short(tool, args)} {count} times with identical args. "
                "Change your approach — different params, different tool, or report why this exact "
                "call needs repeating.",
            )

        return RepeatVerdict(
            "abort",
            sig,
            count,
            f"Identical call to {_short(tool, args)} repeated {count} times after a nudge. "
            "This is a loop — the engagement will stop.",
        )

    def counts(self) -> dict[str, int]:
        return dict(self._counts)


def _sig(tool: str, args: dict) -> str:
    """Stable signature: tool + canonical JSON of args."""
    canonical = json.dumps(args or {}, sort_keys=True, default=str)
    return hashlib.sha256(f"{tool}|{canonical}".encode()).hexdigest()[:16]


def _short(tool: str, args: dict) -> str:
    """Human-readable short form for messages."""
    keys = list((args or {}).keys())[:3]
    return f"{tool}({', '.join(keys)})" if keys else tool


# ── Layer 3: meaningful-progress detector ───────────────────────────────


@dataclass
class ProgressSignals:
    """What counts as progress in a turn."""

    new_finding: bool = False
    new_surface: bool = False
    new_credential: bool = False
    new_foothold: bool = False
    phase_transition: bool = False
    completion_refused: bool = False  # gate dialogue — NOT stuck
    tool_succeeded: bool = False


def is_progress(signals: ProgressSignals) -> bool:
    """True when the turn produced meaningful progress. Gate refusals
    count as dialogue, not stagnation."""
    if signals.completion_refused:
        return True  # the agent decided; a gate pushed back — dialogue
    return (
        signals.new_finding
        or signals.new_surface
        or signals.new_credential
        or signals.new_foothold
        or signals.phase_transition
        or signals.tool_succeeded
    )


def extract_signals(trace_step: dict) -> ProgressSignals:
    """Extract progress signals from a trace step (pure — no state access)."""
    sig = ProgressSignals()
    tool = str(trace_step.get("tool_name") or "")
    success = bool(trace_step.get("success", True))
    obs = str(trace_step.get("tool_output") or "")

    # no tool called = no tool success (the dict default True is for
    # real tool calls; an empty step from a parse failure is NOT progress)
    sig.tool_succeeded = success if tool else False

    if tool == "catalog_exploit" and success and "CONFIRMED" in obs:
        sig.new_finding = True
    if tool == "surface_expand" and success:
        sig.new_surface = True
    if tool in ("creds_add", "record_finding") and success:
        sig.new_credential = True
    if "CONFIRMED" in obs and any(k in obs for k in ("uid=", "whoami", "shell", "password")):
        sig.new_foothold = True
    if "MODE CHANGE" in obs or "phase" in obs.lower()[:30]:
        sig.phase_transition = True

    return sig
