"""Shim — the real module lives at suijin.client.tui.console_ui."""

from suijin.client.tui.console_ui import *  # noqa: F401,F403
from suijin.client.tui.console_ui import (  # noqa: F401
    UI_STATE,
    EngagementUI,
    ask_operator_answer,
    loot_in,
    reset_gauges,
    toggle_reasoning,
)
