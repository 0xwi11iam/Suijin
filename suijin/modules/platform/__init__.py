"""platform — the first core-tier module on the kernel.

Owns: workspace layout, layered config, runtime init, and the service
seam registrations (the proto-Context from Phase 0 graduates here —
its registrations move from init_runtime into module.register()).

This module deliberately lives at suijin/modules/platform/ with NO file
moves: Phase 2 wires existing subsystems onto the kernel via registration
shims; physical moves happen later in the phase once every module rides
the kernel.
"""

from __future__ import annotations

from pathlib import Path

from suijin.kernel.contracts import Module, Tier
from suijin.tools.workspace import WORKSPACE_DIR, ensure_workspace_layout


class PlatformModule(Module):
    id = "platform"
    tier = Tier.CORE

    def __init__(self) -> None:
        self._initialized = False

    def register(self, ctx) -> None:
        """Declare services — cheap, no I/O, no side effects."""

        def _load_config():
            from suijin.core.red.config_loader import load_config

            return load_config()

        ctx.register_service("config", _load_config)
        ctx.register_service("workspace", lambda: Path(ctx.workspace))
        ctx.register_service("llm", lambda: __import__("suijin.tools.providers", fromlist=["generate"]).generate)

        # traffic services (blue scorer etc.) — the Phase 0 seam, reborn
        def _scorer():
            from suijin.core.blue.traffic.scorer import score_request

            return score_request

        ctx.register_service("traffic_scorer", _scorer)

    def start(self, ctx) -> None:
        """One-time process init: module packs, workspace layout, dirs."""
        if self._initialized:
            return
        from suijin.tools.runtime import init_runtime

        init_runtime()
        ensure_workspace_layout()
        for sub in ("payloads", "scripts", "outputs", "reports", "audit_trails"):
            (Path(ctx.workspace) / sub).mkdir(parents=True, exist_ok=True)
        self._initialized = True
        ctx.journal.append("platform", f"workspace ready at {ctx.workspace}")

    def stop(self, ctx) -> None:
        self._initialized = False
