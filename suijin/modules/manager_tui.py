"""Suijin Module Manager — Textual TUI.

Two-pane interface over the management API: tier-grouped module list +
detail pane (deps, permissions, last boot), keys for toggle/install/
uninstall/info/boot report. Pure rendering — every action routes through
suijin.modules.manager (the same API the CLI verbs use).
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static

from suijin.modules import manager as mgmt

TIER_STYLES = {"core": "bold red", "recommended": "bold yellow", "installed": "bold cyan"}


class ModuleManager(App):
    TITLE = "SUIJIN — Module Manager"
    CSS = """
    #body { height: 1fr; }
    #list { width: 55%; border: solid $accent; }
    #detail { width: 45%; border: solid $surface; padding: 1 2; }
    """
    BINDINGS = [
        ("space", "toggle", "Enable/Disable"),
        ("i", "info", "Info"),
        ("d", "deps", "Deps"),
        ("a", "install", "Install"),
        ("x", "uninstall", "Uninstall"),
        ("p", "perms", "Permissions"),
        ("b", "boot", "Boot report"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, module_roots: list[Path] | None = None) -> None:
        super().__init__()
        self._roots = module_roots
        self._entries: list[dict] = []
        self._last_detail: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="list"):
                yield DataTable(id="modules", cursor_type="row", zebra_stripes=True)
            yield Static(id="detail", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#modules", DataTable)
        table.add_columns("Tier", "Module", "Version", "On", "Status")
        self.refresh_table()
        self.set_focus(table)

    def refresh_table(self) -> None:
        table = self.query_one("#modules", DataTable)
        table.clear()
        self._entries = mgmt.list_modules(self._roots)
        for e in sorted(
            self._entries, key=lambda x: ({"core": 0, "recommended": 1, "installed": 2}[x["tier"]], x["id"])
        ):
            status = "ok" if e["enabled"] else "disabled"
            table.add_row(e["tier"], e["id"], e["version"], "●" if e["enabled"] else "○", status, key=e["id"])

    def _selected_id(self) -> str | None:
        table = self.query_one("#modules", DataTable)
        row = table.cursor_row
        if row is None or row < 0 or row >= len(self._entries):
            return None
        return self._entries[row]["id"]

    def _show(self, text: str) -> None:
        self._last_detail = text
        self.query_one("#detail", Static).update(text)

    def on_data_table_row_highlighted(self, event) -> None:
        mid = str(event.row_key.value) if event.row_key and event.row_key.value else None
        if not mid:
            return
        try:
            info = mgmt.module_info(mid, self._roots)
            deps = ", ".join(info["requires"]) or "—"
            perms = ", ".join(info["permissions"]) or "—"
            self._show(
                f"{mid} v{info['version']}\n"
                f"tier: {info['tier']}   enabled: {info['enabled']}\n"
                f"requires: {deps}\n"
                f"provides: {', '.join(info['provides']) or '—'}\n"
                f"permissions: {perms}\n"
                f"source: {info['source']}"
            )
        except mgmt.InstallError as e:
            self._show(str(e))

    def action_toggle(self) -> None:
        mid = self._selected_id()
        if not mid:
            return
        new = not mgmt.is_enabled(mid)
        if mgmt.set_enabled(mid, new):
            self._show(f"{mid}: {'enabled' if new else 'disabled'} (next boot)")
            self.refresh_table()

    def action_info(self) -> None:
        mid = self._selected_id()
        if mid:
            self.on_data_table_row_highlighted(type("E", (), {"row_key": type("K", (), {"value": mid})()})())

    def action_deps(self) -> None:
        mid = self._selected_id()
        if not mid:
            return
        info = mgmt.module_info(mid, self._roots)
        report = mgmt._all_units(self._roots)
        lines = [f"dependencies of {mid}:"]
        for dep in info["requires"]:
            mark = "✓" if dep in report.bootable else "✗"
            lines.append(f"  {mark} {dep}" + (f" (skipped: {report.skipped[dep]})" if dep in report.skipped else ""))
        dependents = [u.id for u in report.units.values() if mid in u.requires]
        if dependents:
            lines.append("required by: " + ", ".join(sorted(dependents)))
        self._show("\n".join(lines))

    async def action_install(self) -> None:
        path = await self.push_screen_wait(InstallDialog())
        if path:
            try:
                self._show(mgmt.install(path))
                self.refresh_table()
            except mgmt.InstallError as e:
                self._show(f"install failed: {e}")

    def action_uninstall(self) -> None:
        mid = self._selected_id()
        if not mid:
            return
        try:
            if mgmt.uninstall(mid):
                self._show(f"{mid}: uninstalled")
                self.refresh_table()
        except mgmt.InstallError as e:
            self._show(f"uninstall refused: {e}")

    def action_perms(self) -> None:
        mid = self._selected_id()
        if not mid:
            return
        info = mgmt.module_info(mid, self._roots)
        perms = "\n".join(f"  • {p}" for p in info["permissions"]) or "  (none declared)"
        self._show(f"permissions declared by {mid}:\n{perms}")

    def action_boot(self) -> None:
        report = mgmt._all_units(self._roots)
        lines = [report.summary()]
        if report.skipped:
            lines.append("skipped:")
            lines += [f"  {k}: {v}" for k, v in report.skipped.items()]
        if report.quarantined:
            lines.append("quarantined:")
            lines += [f"  {k}: {v}" for k, v in report.quarantined.items()]
        self._show("\n".join(lines))


from textual.screen import ModalScreen  # noqa: E402
from textual.widgets import Input  # noqa: E402


class InstallDialog(ModalScreen[str | None]):
    """Path input modal; Enter confirms, Esc cancels."""

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Install module from path:", markup=False),
            Input(placeholder="/path/to/module or ."),
            id="install-dialog",
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


def main() -> None:
    ModuleManager().run()


if __name__ == "__main__":
    main()
