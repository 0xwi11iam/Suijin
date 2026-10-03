"""Supervisor-as-pipeline: rules tier-0, classifier verdict tier-1,
grounded phrasing tier-2 (the only generation left)."""

from suijin.modules.agent.lib.reflex.supervisor.verdict import (
    phrase,
    reflex_enabled,
    shadow_supervisor,
    verdict,
)

__all__ = ["phrase", "reflex_enabled", "shadow_supervisor", "verdict"]
