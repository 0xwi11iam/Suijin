"""Phase 4 integration — disabled modules vanish from a REAL boot."""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MODULES = REPO / "suijin" / "modules"


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    from suijin.modules import manager as mgmt

    monkeypatch.setattr(mgmt, "STATE_DIR", tmp_path / ".suijin")
    monkeypatch.setattr(mgmt, "USER_MODULES", tmp_path / ".suijin" / "modules")
    return mgmt


class TestDisabledBoot:
    def test_disabled_recommended_vanishes(self, isolated_state):
        from suijin.kernel import controller

        isolated_state.set_enabled("redteam", False)
        isolated_state.set_enabled("blueteam", False)
        ctx, report = controller.boot(module_roots=[MODULES],
                                      workspace=Path("/tmp/disboot"), quiet=True)
        assert "redteam" not in report.bootable
        assert "blueteam" not in report.bootable
        assert report.skipped["redteam"] == "disabled by operator"
        # and their console entries genuinely never registered
        hooks = ctx.service("console_hooks")
        assert [e["id"] for e in hooks.menu()] == ["ops"]
        assert not ctx.has_service("mode.red")
        ctx.shutdown()

    def test_disabled_core_aborts(self, isolated_state):
        from suijin.kernel import controller

        isolated_state.set_enabled("platform", False)
        with pytest.raises(RuntimeError, match="core module 'platform' is disabled"):
            controller.boot(module_roots=[MODULES], workspace=Path("/tmp/discore"), quiet=True)

    def test_enabled_default_untouched(self, isolated_state):
        from suijin.kernel import controller

        ctx, report = controller.boot(module_roots=[MODULES],
                                      workspace=Path("/tmp/disok"), quiet=True)
        assert "redteam" in report.bootable
        hooks = ctx.service("console_hooks")
        assert {"redteam", "blueteam", "ops"} == {e["id"] for e in hooks.menu()}
        ctx.shutdown()
