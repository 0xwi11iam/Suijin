"""Packaging guards — the wheel must be complete and versions must not drift.

The 2.9.x-era bugs (SERVER_VERSION hardcoded at 2.3.0-beta for five
releases) came from duplicated version sources; these tests keep the
[project] table and version.json locked together, and the package-data
manifest honest.
"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PKG = REPO / "suijin"


def _pyproject_version() -> str:
    if sys.version_info >= (3, 11):
        import tomllib

        data = tomllib.loads((REPO / "pyproject.toml").read_text())
        return data["project"]["version"]
    try:
        import tomli
    except ImportError:
        pytest.skip("tomli unavailable on this Python")
    data = tomli.loads((REPO / "pyproject.toml").read_text())
    return data["project"]["version"]


def test_pyproject_version_matches_version_json():
    """Two version sources = drift. Keep them locked (see 2.9.x history)."""
    vj = json.loads((PKG / "version.json").read_text())["version"]
    assert _pyproject_version() == vj


def test_console_script_module_exists():
    spec = __import__("importlib.util", fromlist=["util"]).find_spec("suijin.modules.console.lib.cli")
    assert spec is not None


def test_declared_package_data_exists():
    """Every path pattern in [tool.setuptools.package-data] must match at
    least one real file — a stale glob ships a broken wheel silently."""
    import fnmatch

    if sys.version_info < (3, 11):
        pytest.skip("tomllib unavailable")
    import tomllib

    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    pkg_data: dict[str, list[str]] = data["tool"]["setuptools"]["package-data"]
    problems = []
    for package, patterns in pkg_data.items():
        base = REPO / package.replace(".", "/")
        for pat in patterns:
            hit = any(fnmatch.fnmatch(str(f.relative_to(base)), pat) for f in base.rglob("*"))
            if not hit:
                problems.append(f"{package}: {pat} matches nothing")
    assert not problems, "stale package-data patterns:\n" + "\n".join(problems)


def test_entry_point_invocable():
    """The console script target must be callable with no args (argparse path)."""
    from suijin.modules.console.lib.cli import main

    assert callable(main)


def test_dependencies_pinned_like_requirements():
    """requirements.txt entries must all appear in [project].dependencies —
    an installable package that misses a dep is worse than none."""
    if sys.version_info < (3, 11):
        pytest.skip("tomllib unavailable")
    import tomllib

    reqs = {
        line.strip()
        for line in (PKG / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    declared = " ".join(tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["dependencies"])
    missing = [r for r in reqs if r.split(">=")[0].strip().lower() not in declared.lower()]
    assert not missing, f"requirements.txt deps missing from pyproject: {missing}"


class TestDocsSync:
    def test_readme_badge_matches_version(self):
        """Documentation lag is a build failure: the README version badge
        must equal suijin/version.json in the same commit."""
        import json
        import re

        version = json.loads((REPO / "suijin" / "version.json").read_text())["version"]
        readme = (REPO / "README.md").read_text()
        badges = re.findall(r"badge/v([\d.]+)-suijin-green", readme)
        assert badges, "README has no version badge"
        assert all(b == version for b in badges), (
            f"README badge(s) {badges} != version.json {version} — update the badge in the same commit"
        )

    def test_pyproject_matches_version(self):
        import json

        version = json.loads((REPO / "suijin" / "version.json").read_text())["version"]
        pyproject_v = _pyproject_version()  # 3.10-safe (tomli fallback + skip)
        assert pyproject_v == version, f"pyproject {pyproject_v} != version.json {version}"
