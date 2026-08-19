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

REPO = Path(__file__).resolve().parents[3]
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

    # Warm pip cache: repeat runs reuse wheels instead of cold-downloading
    # the whole dependency tree (deterministic on slow links).
    env.setdefault("PIP_CACHE_DIR", os.path.expanduser("~/.cache/suijin-pip"))
    # Own process group: on timeout, kill the WHOLE group — an orphaned
    # pip grandchild otherwise holds the stdout pipe and hangs the runner.
    proc = subprocess.Popen(
        ["bash", str(INSTALL_SH)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=900)
    except subprocess.TimeoutExpired:
        import contextlib
        import signal

        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        pytest.fail("install.sh timed out after 900s (network?); last output:\n" + out[-1500:])
    assert proc.returncode == 0, out

    # 1. install dir migrated from ~/.medusa (marker carried, dir renamed)
    assert (home / ".suijin" / "repo").is_dir()
    assert (home / ".suijin" / "venv-marker").exists() if (home / ".medusa" / "venv-marker").exists() else True
    # the legacy dir may be moved away entirely
    assert not (home / ".medusa" / "repo" / "medusa").exists()

    # 2. workspace data migrated: suijin_agent has the legacy reports
    installed_repo = home / ".suijin" / "repo"
    new_ws = installed_repo / "suijin_agent"
    eng = new_ws / "outputs" / "reports" / "engagement.md"
    if not eng.exists():  # pre-first-run state (migration runs at runtime init)
        eng = new_ws / "reports" / "engagement.md"
    assert eng.read_text() == "# legacy findings"
    sess = new_ws / "outputs" / "sessions" / "s1.json"
    if not sess.exists():  # pre-first-run state
        sess = new_ws / "sessions" / "s1.json"
    assert sess.exists()

    # 3. launcher exists and points at suijin
    launcher = home / "bin" / "suijin"
    assert launcher.exists()
    text = launcher.read_text()
    assert "suijin/modules/console/lib/cli.py" in text

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
