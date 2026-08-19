"""Phase 4 — the module management API: the single source of truth.

install/uninstall/enable/disable/list/info + permissions reporting +
quarantine — one module BOTH the CLI verbs and the Textual manager call.
File-based state (~/.suijin/modules.json is enable/disable; installs in
~/.suijin/modules/), no daemons.
"""

import json
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

from suijin.modules import manager as mgmt  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fake HOME + real module tree copy; manager isolated inside it."""
    home = tmp_path / "home"
    home.mkdir()
    state = home / ".suijin"
    user_modules = state / "modules"
    user_modules.mkdir(parents=True)
    # a small source tree with a real module to install
    src = tmp_path / "src"
    shutil.copytree(REPO / "suijin" / "modules" / "platform", src / "platform")
    monkeypatch.setattr(mgmt, "STATE_DIR", state)
    monkeypatch.setattr(mgmt, "USER_MODULES", user_modules)
    return {"home": home, "state": state, "user_modules": user_modules, "src": src}


def _make_community(env, mid="community"):
    d = env["src"] / mid
    d.mkdir(exist_ok=True)
    (d / "plugin.json").write_text(
        json.dumps(
            {
                "id": mid,
                "version": "1.0",
                "tier": "installed",
                "requires": ["platform"],
                "entry": "pack_entry:community",
            }
        )
    )
    (d / "entry.py").write_text(
        "from suijin.kernel.contracts import Module, Tier\n"
        "class PackModule(Module):\n"
        "    id = '%s'\n    tier = Tier.INSTALLED\n"
        "    def register(self, ctx): pass\n"
        "    def start(self, ctx): pass\n"
        "    def stop(self, ctx): pass\n" % mid
    )
    return d


class TestEnableDisable:
    def test_disable_and_enable(self, env):
        assert mgmt.set_enabled("redteam", False) is True
        assert mgmt.is_enabled("redteam") is False
        assert mgmt.set_enabled("redteam", True) is True
        assert mgmt.is_enabled("redteam") is True

    def test_state_survives_reload(self, env):
        mgmt.set_enabled("knowledge", False)
        # fresh read (no cache) reflects disk
        assert json.loads((env["state"] / "modules.json").read_text())["knowledge"] is False

    def test_unknown_module(self, env):
        assert mgmt.set_enabled("ghost", False) is False


class TestInstall:
    def test_install_from_path(self, env):
        d = _make_community(env)
        msg = mgmt.install(str(d))
        assert "community" in msg and "installed" in msg
        assert (env["user_modules"] / "community" / "plugin.json").exists()

    def test_install_rejects_bad_manifest(self, env):
        bad = env["src"] / "bad"
        bad.mkdir()
        (bad / "plugin.json").write_text("{broken")
        with pytest.raises(mgmt.InstallError, match="manifest"):
            mgmt.install(str(bad))

    def test_install_rejects_core_tier(self, env):
        imp = env["src"] / "imposter"
        imp.mkdir()
        (imp / "plugin.json").write_text(json.dumps({"id": "imposter", "version": "1", "tier": "core", "requires": []}))
        with pytest.raises(mgmt.InstallError, match="core"):
            mgmt.install(str(imp))

    def test_dependencies_reported(self, env):
        needy = env["src"] / "needy"
        needy.mkdir()
        (needy / "plugin.json").write_text(
            json.dumps(
                {
                    "id": "needy",
                    "version": "1.0",
                    "tier": "installed",
                    "requires": ["ghost-dep"],
                    "entry": "",
                    "python_deps": ["pwntools"],
                }
            )
        )
        report = mgmt.install(str(needy), with_deps=False)
        assert "ghost-dep" in report and "missing" in report
        assert "pip install pwntools" in report  # exact command shown


class TestUninstall:
    def test_uninstall_installed_module(self, env):
        d = _make_community(env)
        mgmt.install(str(d))
        assert mgmt.uninstall("community") is True
        assert not (env["user_modules"] / "community").exists()

    def test_uninstall_builtin_refused(self, env):
        with pytest.raises(mgmt.InstallError, match="bundled"):
            mgmt.uninstall("platform")  # wheel module, never in user space


class TestListInfo:
    def test_list_includes_tiers(self, env):
        entries = mgmt.list_modules(module_roots=[REPO / "suijin" / "modules"])
        ids = {e["id"] for e in entries}
        assert {"platform", "redteam"} <= ids
        redteam = next(e for e in entries if e["id"] == "redteam")
        assert redteam["tier"] == "recommended"
        assert redteam["enabled"] is True

    def test_info_shape(self, env):
        info = mgmt.module_info("redteam", module_roots=[REPO / "suijin" / "modules"])
        assert info["id"] == "redteam"
        assert info["requires"] == ["agent", "providers"]
        assert isinstance(info["permissions"], list)

    def test_permissions_reported(self, env):
        info = mgmt.module_info("tools", module_roots=[REPO / "suijin" / "modules"])
        assert "shell" in info["permissions"]
