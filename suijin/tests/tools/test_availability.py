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


class TestOSInstallHints:
    """install_hint is OS-tailored: brew on macOS, apt/dnf adapted on
    Linux, and macOS NEVER gets an apt line while a note exists."""

    def test_detect_brew_on_macos(self, monkeypatch):
        from suijin.modules.tools.lib import availability as av

        monkeypatch.setattr(av.platform, "system", lambda: "Darwin")
        assert av.detect_package_manager() == "brew"

    def test_detect_dnf_on_fedora(self, monkeypatch):
        from suijin.modules.tools.lib import availability as av

        monkeypatch.setattr(av.platform, "system", lambda: "Linux")
        monkeypatch.setattr(av, "_os_release_text", lambda: 'name="fedora linux" id=fedora')
        assert av.detect_package_manager() == "dnf"

    def test_hint_adapts_apt_to_dnf(self, monkeypatch):
        from suijin.modules.tools.lib import availability as av

        monkeypatch.setattr(av, "detect_package_manager", lambda: "dnf")
        assert av.install_hint("nmap") == "sudo dnf install nmap"

    def test_macos_never_gets_apt_when_note_exists(self, monkeypatch):
        from suijin.modules.tools.lib import availability as av

        monkeypatch.setattr(av, "detect_package_manager", lambda: "brew")
        hint = av.install_hint("curl")  # note-only entry, no apt anywhere
        assert "apt" not in hint and "built into" in hint

    def test_macos_hint_prefers_brew(self, monkeypatch):
        from suijin.modules.tools.lib import availability as av

        monkeypatch.setattr(av, "detect_package_manager", lambda: "brew")
        assert av.install_hint("smbclient") == "brew install samba"
        assert av.install_hint("medusa") == "brew install medusa"
        assert av.install_hint("dig") == "brew install bind"
        assert av.install_hint("snmpwalk") == "brew install net-snmp"
        assert av.install_hint("metasploit") == "brew install metasploit"

    def test_cme_hints_at_netexec(self, monkeypatch):
        from suijin.modules.tools.lib import availability as av

        monkeypatch.setattr(av, "detect_package_manager", lambda: "brew")
        hint = av.install_hint("crackmapexec")
        assert "NetExec" in hint and "alias crackmapexec" in hint  # PyPI original is dead

    def test_apt_hint_adapts_bind9(self, monkeypatch):
        from suijin.modules.tools.lib import availability as av

        monkeypatch.setattr(av, "detect_package_manager", lambda: "apt")
        assert av.install_hint("dig") == "sudo apt install bind9-dnsutils"

    def test_pip_name_aliases_resolve(self):
        """duckduckgo-search imports as duckduckgo_search — the pip name
        must not read as missing when the package is installed."""
        from suijin.modules.tools.lib.availability import _dependency_available

        assert _dependency_available("duckduckgo-search")  # core dep, always in the venv

    def test_which_alias_metasploit(self, monkeypatch):
        """The metasploit dep resolves via the msfconsole binary it pours."""
        from suijin.modules.tools.lib import availability as av

        monkeypatch.setattr(av.shutil, "which", lambda b: "/opt/homebrew/bin/msfconsole" if b == "msfconsole" else None)
        assert av._dependency_available("metasploit")
        assert av._dependency_available("metasploit-framework")

    def test_pip_hints_name_the_running_interpreter(self, monkeypatch):
        """A bare `pip install` lands in whatever python is first on PATH —
        on multi-python hosts the dep then reads missing forever. Hints
        must target the interpreter doctor itself runs from."""
        from suijin.modules.tools.lib import availability as av

        monkeypatch.setattr(av, "detect_package_manager", lambda: "brew")
        monkeypatch.setattr(av.sys, "executable", "/opt/suijin/venv/bin/python")
        hint = av.install_hint("gvm-tools")
        assert hint == "/opt/suijin/venv/bin/python -m pip install gvm-tools"

    def test_brew_hints_not_prefixed(self, monkeypatch):
        from suijin.modules.tools.lib import availability as av

        monkeypatch.setattr(av, "detect_package_manager", lambda: "brew")
        assert av.install_hint("nmap") == "brew install nmap"  # no interpreter prefix
