"""Shim — the real module lives at suijin.client.tui.session_control."""

from suijin.client.tui.session_control import *  # noqa: F401,F403
from suijin.client.tui.session_control import (  # noqa: F401
    PauseContext,
    build_attack_chains,
    force_report,
    pause_console,
)
