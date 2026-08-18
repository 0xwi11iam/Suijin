"""console — the surfaces core module (TUI/CLI/WebUI/MCP).

Feature-blind by contract: menus and verbs come from OTHER modules via
the ConsoleHooks registry. This is the mechanism that makes disable =
disappear real: a module's stop() (or its absence from boot) removes its
surface entries automatically because they were never hardcoded here.
"""

from __future__ import annotations

from suijin.kernel.contracts import Module, Tier


class ConsoleHooks:
    """Extension registry owned by console; consumed by every surface."""

    def __init__(self) -> None:
        self._menu: dict[str, dict] = {}
        self._verbs: dict[str, object] = {}

    def register_menu(self, mid: str, label: str = "", order: int = 100, owner: str = "") -> None:
        self._menu[mid] = {"id": mid, "label": label or mid, "order": order, "owner": owner or mid}

    def register_verb(self, name: str, fn, owner: str = "") -> None:
        self._verbs[name] = {"fn": fn, "owner": owner or name}

    def unregister_owner(self, owner: str) -> None:
        """Remove every entry a module registered (its stop() calls this)."""
        self._menu = {k: v for k, v in self._menu.items() if v["owner"] != owner}
        self._verbs = {k: v for k, v in self._verbs.items() if v["owner"] != owner}

    def menu(self) -> list[dict]:
        return [self._menu[k] for k in sorted(self._menu, key=lambda k: (self._menu[k]["order"], k))]

    def verb_names(self) -> list[str]:
        return sorted(self._verbs)

    def run_verb(self, name: str):
        entry = self._verbs.get(name)
        if entry is None:
            return None
        return entry["fn"]()


class ConsoleModule(Module):
    id = "console"
    tier = Tier.CORE

    def register(self, ctx) -> None:
        hooks = ConsoleHooks()
        ctx.register_service("console_hooks", lambda: hooks)

        def _run():
            from suijin.main import main as tui_main

            return tui_main()

        ctx.register_service("console.run", lambda: _run)

    def start(self, ctx) -> None:
        ctx.journal.append("console", "hook registry ready")

    def stop(self, ctx) -> None:
        hooks = ctx.service("console_hooks")
        if hooks:
            hooks._menu.clear()
            hooks._verbs.clear()
