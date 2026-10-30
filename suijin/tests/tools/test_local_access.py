"""Local access (2026-10-30 operator ruling) — real paths, hard floor.

The old blanket mirror made every outside write land in a fake engagement
copy ('the directorying is a mess'). Now: absolute non-system paths pass
through REAL by default; the system floor never opens; contained mode is
one config key away.
"""

import pytest

from suijin.server.confinement import is_system_path, jail_path


@pytest.fixture()
def _eng(tmp_path, monkeypatch):
    (tmp_path / "eng").mkdir()
    monkeypatch.setattr("suijin.server.confinement._engagement_root", lambda root=None: tmp_path / "eng")
    return tmp_path


class TestLocalAccess:
    def test_real_paths_pass_through_by_default(self, _eng, monkeypatch, tmp_path):
        monkeypatch.setattr("suijin.server.confinement.local_access_enabled", lambda: True)
        target = tmp_path / "projects" / "tool.go"
        p = jail_path(str(target))
        assert p.resolve() == target.resolve()  # REAL path (macOS /var symlink-safe), not mirrored

    def test_system_floor_never_opens(self, _eng, monkeypatch):
        monkeypatch.setattr("suijin.server.confinement.local_access_enabled", lambda: True)
        for sysp in ("/etc/passwd", "/System/Library/x", "/usr/bin/x", "/bin/sh", "/Library/Preferences/x"):
            jailed = jail_path(sysp)
            assert str(jailed).startswith(str(_eng / "eng" / "home")), f"{sysp} must stay mirrored"

    def test_is_system_path(self, tmp_path):
        assert is_system_path("/etc/hosts")
        assert is_system_path("/usr/local/bin/x")
        assert not is_system_path(str(tmp_path / "mine.txt"))
        assert not is_system_path("/Users/whoever/projects/x")

    def test_contained_mode_mirrors(self, _eng, monkeypatch):
        monkeypatch.setattr("suijin.server.confinement.local_access_enabled", lambda: False)
        p = jail_path(str(_eng / "outside" / "x.txt"))
        assert str(p).startswith(str(_eng / "eng" / "home"))

    def test_inside_engagement_always_unchanged(self, _eng, monkeypatch):
        monkeypatch.setattr("suijin.server.confinement.local_access_enabled", lambda: True)
        inside = _eng / "eng" / "home" / "notes.md"
        assert jail_path(str(inside)) == inside

    def test_relative_paths_stay_in_userland(self, _eng, monkeypatch):
        monkeypatch.setattr("suijin.server.confinement.local_access_enabled", lambda: True)
        p = jail_path("scratch/x.txt")
        assert str(p).startswith(str(_eng / "eng" / "home"))


class TestExecutorEnv:
    def test_local_access_keeps_real_home(self, _eng, monkeypatch):
        import os

        from suijin.server.confinement import executor_env

        monkeypatch.setattr("suijin.server.confinement.local_access_enabled", lambda: True)
        env = executor_env()
        assert env["HOME"] == os.environ["HOME"]  # the REAL home

    def test_contained_mode_confines_home(self, _eng, monkeypatch):
        from suijin.server.confinement import executor_env

        monkeypatch.setattr("suijin.server.confinement.local_access_enabled", lambda: False)
        env = executor_env()
        assert (
            "engagements" in env["HOME"] or str(_eng) in env["HOME"] or env["HOME"] != __import__("os").environ["HOME"]
        )
