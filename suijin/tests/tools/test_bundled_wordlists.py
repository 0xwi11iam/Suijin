"""Bundled wordlists — every engagement home ships ~/wordlists/.

Live-run evidence (2026-10-30): the agent referenced
~/wordlists/api-endpoints.txt that didn't exist. The bundle seeds
idempotently and NEVER overwrites operator-curated lists.
"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _pinned(tmp_path, monkeypatch):
    import suijin.modules.platform.lib.workspace as ws

    (tmp_path / "eng" / "home").mkdir(parents=True)
    monkeypatch.setattr(ws, "engagement_dir", lambda: tmp_path / "eng")
    return ws


class TestBundledWordlists:
    def test_home_ships_the_bundled_sets(self, _pinned):
        h = _pinned.home_dir()
        common = h / "wordlists" / "common.txt"
        api = h / "wordlists" / "api-endpoints.txt"
        assert common.is_file() and api.is_file()
        lines = common.read_text().splitlines()
        assert len(lines) >= 250 and "admin" in lines and ".git" in lines
        assert len(api.read_text().splitlines()) >= 120

    def test_curated_lists_never_overwritten(self, _pinned):
        h = _pinned.home_dir()
        curated = h / "wordlists" / "common.txt"
        curated.write_text("operator-own-list\n")
        _pinned.home_dir()  # re-run the seed
        assert curated.read_text() == "operator-own-list\n"

    def test_repo_bundle_exists(self):
        bundle = Path(__file__).resolve().parents[3] / "suijin" / "server" / "tools" / "lib" / "wordlists"
        assert (bundle / "common.txt").is_file() and (bundle / "api-endpoints.txt").is_file()
