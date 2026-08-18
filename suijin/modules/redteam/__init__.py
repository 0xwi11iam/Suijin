"""redteam — the offensive mode module (recommended tier).

Registers its console surface (menu entry + verbs) via hooks — disabling
this module removes Red Team from the menu and its verbs from the CLI,
for real.
"""

from __future__ import annotations

from suijin.kernel.contracts import Module, Tier


class RedTeamModule(Module):
    id = "redteam"
    tier = Tier.RECOMMENDED

    def register(self, ctx) -> None:
        ctx.register_service(
            "mode.red", lambda: __import__("suijin.core.redteamer", fromlist=["run_red_team"]).run_red_team
        )

    def start(self, ctx) -> None:
        hooks = ctx.service("console_hooks")
        if hooks is None:
            return  # console absent (headless boot) — silently fine

        def _launch():
            from suijin.core.redteamer import main as rt_main

            return rt_main()

        hooks.register_menu("redteam", label="Red Team (Autonomous Agent)", order=10, owner="redteam")
        hooks.register_verb("redteam", _launch, owner="redteam")
        ctx.journal.append("redteam", "menu + verb registered")

    def stop(self, ctx) -> None:
        hooks = ctx.service("console_hooks")
        if hooks is not None:
            hooks.unregister_owner("redteam")
