"""ops — operator tooling module (recommended tier).

The v2.9–v2.11 engagement-lifecycle verbs, registered as console verbs:
export, debrief, replay, dossier, timeline, battle, approvals, panic,
scope, clean, notify.
"""

from __future__ import annotations

from suijin.kernel.contracts import Module, Tier

_VERBS = (
    "export",
    "debrief",
    "replay",
    "dossier",
    "timeline",
    "battle",
    "approvals",
    "panic",
    "scope",
    "clean",
    "notify",
)


class OpsModule(Module):
    id = "ops"
    tier = Tier.RECOMMENDED

    def register(self, ctx) -> None:
        for verb in _VERBS:
            ctx.register_service(
                f"ops.{verb}",
                lambda v=verb: __import__("suijin.cli", fromlist=["main"]).main,
            )

    def start(self, ctx) -> None:
        hooks = ctx.service("console_hooks")
        if hooks is None:
            return
        hooks.register_menu("ops", label="Operator Tools", order=30, owner="ops")
        ctx.journal.append("ops", f"{len(_VERBS)} operator verbs available")

    def stop(self, ctx) -> None:
        hooks = ctx.service("console_hooks")
        if hooks is not None:
            hooks.unregister_owner("ops")
