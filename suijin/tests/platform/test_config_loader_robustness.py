"""config_loader robustness — a bad config path must NEVER crash boot.

The field bug: docker compose bind-mounts ./suijin/config.json into the
container; when the host file is absent Docker creates a DIRECTORY at
the mount point, and load_config() (which only checked .exists()) died
with IsADirectoryError — a raw traceback wall on every container boot.
Same tolerance for corrupt JSON, non-object JSON, and read-only mounts.
"""

import json
import os
import subprocess
import sys

import pytest

import suijin.modules.platform.lib.config_loader as cl


@pytest.fixture(autouse=True)
def _paths(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cl, "ENV_PATH", tmp_path / ".env")
    yield tmp_path


class TestBadConfigPathBoots:
    def test_config_json_is_a_directory(self, tmp_path, capsys):
        """THE container bug: compose created a directory at the mount
        point — defaults come back, one clear warning, no traceback."""
        cl.CONFIG_PATH.mkdir()
        cfg = cl.load_config()
        assert cfg.get("provider") == "deepseek"  # usable defaults
        assert "DIRECTORY" in capsys.readouterr().out  # the operator is told why

    def test_corrupt_json_falls_back_to_defaults(self, capsys):
        cl.CONFIG_PATH.write_text("{not json at all")
        cfg = cl.load_config()
        assert cfg.get("provider") == "deepseek"
        assert "unreadable" in capsys.readouterr().out

    def test_non_object_json_falls_back(self, capsys):
        cl.CONFIG_PATH.write_text(json.dumps(["a", "list"]))
        cfg = cl.load_config()
        assert cfg.get("provider") == "deepseek"
        assert "unreadable" in capsys.readouterr().out

    def test_directory_is_never_overwritten(self):
        cl.CONFIG_PATH.mkdir()
        cl.load_config()
        assert cl.CONFIG_PATH.is_dir()  # untouched — never rm/rmdir a mount

    def test_absent_file_seeds_defaults_file_with_fresh_install_caps(self):
        cfg = cl.load_config()
        assert cl.CONFIG_PATH.is_file()  # seeded
        assert cfg["cost_budget_usd"] == 1.0 and cfg["cost_hard_cap_usd"] == 2.0  # fresh-install posture
        on_disk = json.loads(cl.CONFIG_PATH.read_text())
        assert on_disk["provider"] == "deepseek"

    def test_unwritable_location_rides_in_memory(self, tmp_path):
        """Read-only mount (compose ':ro'): the seed write fails silently
        and the defaults still flow."""
        cl.CONFIG_PATH = tmp_path / "no" / "such" / "dir" / "config.json"
        cfg = cl.load_config()
        assert cfg.get("provider") == "deepseek"

    def test_env_path_as_directory_does_not_crash(self):
        cl.ENV_PATH.mkdir()
        cl.load_env()  # parses nothing, raises nothing


class TestPathOverrides:
    """SUIJIN_CONFIG / SUIJIN_ENV redirect the settings and secrets files.

    Resolved at import, so each case runs in a fresh interpreter — which
    is also the honest test: this is exactly how a caller uses it.
    """

    def _paths(self, tmp_path, **env):
        code = "import suijin.modules.platform.lib.config_loader as cl;print(cl.CONFIG_PATH);print(cl.ENV_PATH)"
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, **env},
            timeout=120,
        )
        assert out.returncode == 0, out.stderr[-500:]
        cfg, dotenv = out.stdout.strip().splitlines()[-2:]
        return cfg, dotenv

    def test_overrides_are_honored(self, tmp_path):
        cfg, dotenv = self._paths(
            tmp_path,
            SUIJIN_CONFIG=str(tmp_path / "alt.json"),
            SUIJIN_ENV=str(tmp_path / "alt.env"),
        )
        assert cfg == str(tmp_path / "alt.json")
        assert dotenv == str(tmp_path / "alt.env")

    def test_absent_overrides_keep_the_in_tree_defaults(self, tmp_path):
        """No env ⇒ the operator's own config.json/.env, unchanged."""
        env = {k: v for k, v in os.environ.items() if k not in ("SUIJIN_CONFIG", "SUIJIN_ENV")}
        code = "import suijin.modules.platform.lib.config_loader as cl;print(cl.CONFIG_PATH);print(cl.ENV_PATH)"
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
        assert out.returncode == 0, out.stderr[-500:]
        cfg, dotenv = out.stdout.strip().splitlines()[-2:]
        assert cfg.endswith("config.json") and "suijin/" in cfg
        assert dotenv.endswith(".env") and "suijin/" in dotenv

    def test_one_override_leaves_the_other_alone(self, tmp_path):
        cfg, dotenv = self._paths(tmp_path, SUIJIN_CONFIG=str(tmp_path / "only.json"))
        assert cfg == str(tmp_path / "only.json")
        assert dotenv.endswith(".env")

    def test_the_override_is_what_load_config_reads(self, tmp_path):
        """It is not just a constant: the loader follows it end to end."""
        target = tmp_path / "profile.json"
        target.write_text(json.dumps({"provider": "qwen_cn"}), encoding="utf-8")
        code = "import suijin.modules.platform.lib.config_loader as cl;print(cl.load_config().get('provider'))"
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "SUIJIN_CONFIG": str(target)},
            timeout=120,
        )
        assert out.returncode == 0, out.stderr[-500:]
        assert "qwen_cn" in out.stdout


class TestReadOnlyMountWrites:
    def test_write_config_warns_on_oserror(self, tmp_path, capsys):
        cl.CONFIG_PATH = tmp_path / "no" / "such" / "dir" / "config.json"
        assert cl._write_config({"provider": "x"}) is False
        assert "read-only" in capsys.readouterr().out
