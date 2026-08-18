"""blueteam — the defensive mode module (recommended tier)."""

from __future__ import annotations

from suijin.kernel.contracts import Module, Tier


class BlueTeamModule(Module):
    id = "blueteam"
    tier = Tier.RECOMMENDED

    def register(self, ctx) -> None:
        ctx.register_service("mode.blue", lambda: __import__("suijin.core.blueteamer", fromlist=["main"]).main)

    def start(self, ctx) -> None:
        hooks = ctx.service("console_hooks")
        if hooks is None:
            return
        hooks.register_menu("blueteam", label="Blue Team (Active Defense)", order=20, owner="blueteam")

        def _launch():
            from suijin.core.blueteamer import main as bt_main

            return bt_main()

        hooks.register_verb("blueteam", _launch, owner="blueteam")
        ctx.journal.append("blueteam", "menu + verb registered")

    def stop(self, ctx) -> None:
        hooks = ctx.service("console_hooks")
        if hooks is not None:
            hooks.unregister_owner("blueteam")
