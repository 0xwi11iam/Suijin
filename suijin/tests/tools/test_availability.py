"""Tests for suijin/tools/availability.py — tool-to-binary mapping."""

from suijin.modules.loader import discover_modules
from suijin.modules.tools.lib.availability import (
    install_hint,
    missing_binaries,
    tool_dependencies,
    unavailable_tool_names,
)


def setup_function():
    discover_modules()


class TestToolDependencies:
    def test_nmap_requires_nmap_binary(self):
        deps = tool_dependencies()
        assert "nmap_scan" in deps
        assert "nmap" in deps["nmap_scan"]

    def test_missing_binaries_is_a_dict(self):
        missing = missing_binaries()
        assert isinstance(missing, dict)
        # Every missing tool lists at least one binary
        for tool, binaries in missing.items():
            assert binaries

    def test_unavailable_tool_names_is_a_set(self):
        names = unavailable_tool_names()
        assert isinstance(names, set)

    def test_install_hint_nonempty(self):
        assert install_hint("nmap")
        assert install_hint("totally_unknown_binary")
