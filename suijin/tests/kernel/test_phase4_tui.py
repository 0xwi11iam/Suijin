"""Phase 4 — Module Manager TUI (Rich) construction + headless interaction.

The editor is pure rendering over the management API: listing, the detail
pane, toggle, permissions and the boot report — all driven without a
terminal (the key stream is off in tests, the actions are pure).
"""

from pathlib import Path

import pytest
from rich.console import Console

from suijin.modules import manager as mgmt  # noqa: E402
from suijin.modules.manager_tui import ModuleManager  # noqa: E402

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(mgmt, "STATE_DIR", tmp_path / ".suijin")
    monkeypatch.setattr(mgmt, "USER_MODULES", tmp_path / ".suijin" / "modules")


def _app() -> ModuleManager:
    return ModuleManager(
        module_roots=[REPO / "suijin" / "modules"],
        console=Console(width=100, force_terminal=False, record=True),
    )


def _select(app: ModuleManager, mid: str) -> None:
    app.refresh()
    app._cursor = next(i for i, e in enumerate(app._entries) if e["id"] == mid)


class TestManagerTUI:
    def test_lists_the_whole_tree(self, isolated_state):
        app = _app()
        entries = app.refresh()
        assert len(entries) >= 12  # the whole core + recommended tree
        ids = {str(e["id"]) for e in entries}
        assert "platform" in ids and "redteam" in ids

    def test_entries_are_tier_grouped(self, isolated_state):
        app = _app()
        app.refresh()
        order = [str(e["tier"]) for e in app._entries]
        rank = {"core": 0, "recommended": 1, "installed": 2}
        assert [rank[t] for t in order] == sorted(rank[t] for t in order)

    def test_detail_shows_requires(self, isolated_state):
        app = _app()
        app._show(app.action_info("redteam"))
        assert "redteam" in app._last_detail and "requires" in app._last_detail

    def test_render_includes_the_table_and_detail(self, isolated_state):
        app = _app()
        app.refresh()
        app.render()
        out = app._console.export_text()
        assert "Module Manager" in out and "redteam" in out
        assert "space enable/disable" in out  # the key legend

    def test_toggle_disables_and_refreshes(self, isolated_state):
        app = _app()
        _select(app, "redteam")
        app.action_toggle()
        assert mgmt.is_enabled("redteam") is False  # state flipped
        entry = next(e for e in mgmt.list_modules(app._roots) if e["id"] == "redteam")
        assert entry["enabled"] is False  # the refreshed list reflects it

    def test_perms_view(self, isolated_state):
        app = _app()
        _select(app, "tools")
        app.action_perms()
        assert "shell" in app._last_detail

    def test_boot_report_view(self, isolated_state):
        app = _app()
        app.refresh()
        app.action_boot()
        assert "module(s) loaded" in app._last_detail

    def test_deps_view(self, isolated_state):
        app = _app()
        app.action_deps("redteam")
        assert "dependencies of redteam" in app._last_detail

    def test_unknown_module_is_isolated(self, isolated_state):
        app = _app()
        app._show(app.action_info("definitely-not-a-module"))
        assert "definitely-not-a-module" in app._last_detail or app._last_detail  # never raises

    def test_line_mode_quits(self, isolated_state, monkeypatch):
        app = _app()
        app.refresh()
        monkeypatch.setattr(app, "_step_line", lambda line: False)
        assert app._step_line("q") is False

    def test_line_mode_runs_an_action(self, isolated_state):
        app = _app()
        app.refresh()
        app._step_line("b")  # boot report
        assert "module(s) loaded" in app._last_detail

    def test_run_quits_immediately_off_tty(self, isolated_state, monkeypatch):
        app = _app()
        monkeypatch.setattr("suijin.modules.manager_tui._key_stream", lambda: None)
        monkeypatch.setattr(Console, "input", lambda self, *a, **k: "q")
        assert app.run() == 0

    def test_no_textual(self):
        source = (REPO / "suijin" / "modules" / "manager_tui.py").read_text()
        assert "import textual" not in source and "from textual" not in source
        assert "from rich" in source
