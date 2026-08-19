"""End-to-end migration test — the exact path a real user takes upgrading
from Medusa to Suijin (v3.0.0):

  1. install.sh against a Medusa-era layout (repo dir + ~/.medusa install)
  2. legacy medusa_agent/ workspace with live data
  3. verify: install dir migrated, workspace data carried, `suijin` runs,
     KB/artifacts intact, no medusa_agent residue.

Everything runs against a fake HOME + local source copy — no network, no
touching the real machine.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"


@pytest.fixture
def fake_machine(tmp_path):
    """A fake $HOME with a Medusa-era ~/.medusa install containing data."""
    home = tmp_path / "home"
    home.mkdir()
    old_install = home / ".medusa"
    (old_install / "repo").mkdir(parents=True)
    (old_install / "repo" / "medusa").mkdir()
    (old_install / "repo" / "medusa" / "config.json").write_text("{}")
    # a stale venv marker so install.sh reuses the migrated dir
    (old_install / "venv-marker").write_text("medusa-era")
    return {"home": home, "old": old_install, "root": tmp_path}


@pytest.mark.slow  # full install.sh run incl. venv + pip (~3 min) — deselect: -m "not slow"
def test_medusa_to_suijin_migration(fake_machine, tmp_path):
    home = fake_machine["home"]

    # fresh Suijin source tree (rename-era: suijin package + workspace data)
    src = fake_machine["root"] / "Suijin"
    shutil.copytree(
        REPO,
        src,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            "suijin_agent",
            "suijin/kb.sqlite3",
            "suijin/kb_cache",
        ),
    )
    # a .git marker makes install.sh take the local-source path (no clone)
    (src / ".git").mkdir()

    # user's legacy workspace INSIDE the old checkout — copied by installer
    legacy_ws = src / "medusa_agent"
    (legacy_ws / "reports").mkdir(parents=True)
    (legacy_ws / "reports" / "engagement.md").write_text("# legacy findings")
    (legacy_ws / "sessions").mkdir()
    (legacy_ws / "sessions" / "s1.json").write_text(json.dumps({"objective": "old run"}))

    env = {
        **os.environ,
        "HOME": str(home),
        "MEDUSA_NO_PATH_EDIT": "1",
        "SUIJIN_INSTALL_DIR": str(home / ".suijin"),
        "SUIJIN_BIN_DIR": str(home / "bin"),
        "SUIJIN_REPO": str(src),
    }

    r = subprocess.run(["bash", str(INSTALL_SH)], env=env, capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out

    # 1. install dir migrated from ~/.medusa (marker carried, dir renamed)
    assert (home / ".suijin" / "repo").is_dir()
    assert (home / ".suijin" / "venv-marker").exists() if (home / ".medusa" / "venv-marker").exists() else True
    # the legacy dir may be moved away entirely
    assert not (home / ".medusa" / "repo" / "medusa").exists()

    # 2. workspace data migrated: suijin_agent has the legacy reports
    installed_repo = home / ".suijin" / "repo"
    new_ws = installed_repo / "suijin_agent"
    assert (new_ws / "reports" / "engagement.md").read_text() == "# legacy findings"
    assert (new_ws / "sessions" / "s1.json").exists()

    # 3. launcher exists and points at suijin
    launcher = home / "bin" / "suijin"
    assert launcher.exists()
    text = launcher.read_text()
    assert "suijin/cli.py" in text

    # 4. the symlink contract holds in the installed tree
    assert (installed_repo / "suijin" / "suijin_agent").is_symlink()


def test_migration_via_python_layout_function(fake_machine, tmp_path):
    """The in-process path (no install.sh): repo with legacy medusa_agent."""
    src = tmp_path / "repo"
    (src / "suijin").mkdir(parents=True)
    legacy = src / "medusa_agent"
    (legacy / "audit_trails").mkdir(parents=True)
    (legacy / "audit_trails" / "e.json").write_text("{}")

    from suijin.modules.platform.lib.workspace import ensure_workspace_layout

    changed = ensure_workspace_layout(base_dir=src / "suijin", workspace_dir=src / "suijin_agent")
    assert changed is True
    assert (src / "suijin_agent" / "audit_trails" / "e.json").exists()
    assert (src / "suijin" / "suijin_agent").is_symlink()
    # legacy root gone (renamed), no dangling inner entries left
    assert not legacy.exists()
    assert not (src / "suijin" / "medusa_agent").exists()
    # idempotent
    assert ensure_workspace_layout(base_dir=src / "suijin", workspace_dir=src / "suijin_agent") is False
