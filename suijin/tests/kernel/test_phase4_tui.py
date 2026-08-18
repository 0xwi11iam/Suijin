"""Phase 4 — Module Manager TUI construction + headless interaction.

Textual apps can be driven programmatically via run_test(): mount,
navigate, toggle, verify the table + detail pane without a terminal.
"""

import pytest

pytest.importorskip("textual")

from pathlib import Path  # noqa: E402

from suijin.modules import manager as mgmt  # noqa: E402
from suijin.modules.manager_tui import ModuleManager  # noqa: E402

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(mgmt, "STATE_DIR", tmp_path / ".suijin")
    monkeypatch.setattr(mgmt, "USER_MODULES", tmp_path / ".suijin" / "modules")


class TestManagerTUI:
    async def test_mounts_and_lists(self, isolated_state):
        app = ModuleManager(module_roots=[REPO / "suijin" / "modules"])
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#modules")
            assert table.row_count >= 12  # the whole core + recommended tree
            ids = {str(app._entries[i]["id"]) for i in range(table.row_count)}
            assert "platform" in ids and "redteam" in ids

    async def test_detail_on_highlight(self, isolated_state):
        app = ModuleManager(module_roots=[REPO / "suijin" / "modules"])
        async with app.run_test() as pilot:
            await pilot.pause()
            app._show("probe")
            # simulate row highlight for redteam
            app.on_data_table_row_highlighted(
                type("E", (), {"row_key": type("K", (), {"value": "redteam"})()})())
            assert "redteam" in app._last_detail and "requires" in app._last_detail

    async def test_toggle_disables_and_refreshes(self, isolated_state):
        app = ModuleManager(module_roots=[REPO / "suijin" / "modules"])
        async with app.run_test() as pilot:
            await pilot.pause()
            app._entries = mgmt.list_modules(app._roots)
            table = app.query_one("#modules")
            row = next(i for i, e in enumerate(app._entries) if e["id"] == "redteam")
            table.move_cursor(row=row)
            app.action_toggle()
            await pilot.pause()
            assert mgmt.is_enabled("redteam") is False  # state flipped
            # refreshed entries reflect the disable
            entry = next(e for e in mgmt.list_modules(app._roots) if e["id"] == "redteam")
            assert entry["enabled"] is False

    async def test_perms_view(self, isolated_state):
        app = ModuleManager(module_roots=[REPO / "suijin" / "modules"])
        async with app.run_test() as pilot:
            await pilot.pause()
            app._entries = mgmt.list_modules(app._roots)
            table = app.query_one("#modules")
            row = next(i for i, e in enumerate(app._entries) if e["id"] == "tools")
            table.move_cursor(row=row)
            app.action_perms()
            assert "shell" in app._last_detail

    async def test_boot_report_view(self, isolated_state):
        app = ModuleManager(module_roots=[REPO / "suijin" / "modules"])
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_boot()
            assert "module(s) loaded" in app._last_detail
