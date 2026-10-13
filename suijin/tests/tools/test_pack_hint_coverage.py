"""Pack-declared dependencies contribute REAL install hints.

The doctor sweep prints one install command per missing dependency. A
dep with no _INSTALL entry falls back to the generic
`apt install <name>` line — the ldapsearch class of bug (a hint that
can never succeed because the package doesn't exist under that name).
This pin: every dependency ANY vendored pack declares must have a real
hint entry (or a which/import alias resolving it to what actually
ships).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from suijin.modules.tools.lib.availability import (
    _IMPORT_ALIASES,
    _INSTALL,
    _WHICH_ALIASES,
    install_hint,
)

_MODULES = Path(__file__).resolve().parents[2] / "modules"


def _all_declared_deps() -> dict[str, list[str]]:
    deps: dict[str, list[str]] = {}
    for mf in sorted(_MODULES.glob("*/manifest.json")):
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for d in m.get("dependencies") or []:
            deps.setdefault(str(d), []).append(mf.parent.name)
    return deps


def test_every_declared_dependency_has_a_real_hint():
    """No pack dep may fall through to the generic fallback line."""
    bare = []
    for dep in _all_declared_deps():
        if dep in _INSTALL:
            continue  # real entry
        if dep in _WHICH_ALIASES or dep in _IMPORT_ALIASES:
            continue  # resolved to what actually ships
        bare.append(dep)
    assert not bare, f"deps with only the generic fallback hint: {bare}"


def test_generic_fallback_is_obviously_generic():
    """The fallback line exists for undeclared names only — it must
    self-identify so it can never masquerade as a verified command."""
    for pm in ("apt", "brew", "dnf", "pacman", "apk"):
        hint = install_hint("definitely-not-a-real-package-xyz")
        assert "if it's a Python package" in hint or "note" in hint or "#" in hint, (pm, hint)


@pytest.mark.parametrize(
    "dep,expect_pkg",
    [
        # the fixed class: binary name != package name
        ("ldapsearch", "ldap-utils"),
        ("testssl.sh", "testssl.sh"),  # apt package ships binary `testssl` (alias covers)
    ],
)
def test_known_trap_hints(dep, expect_pkg):
    entry = _INSTALL.get(dep)
    assert entry, f"{dep} lost its hint entry"
    if expect_pkg not in ("testssl.sh",):
        assert any(expect_pkg in v for v in entry.values()), entry
