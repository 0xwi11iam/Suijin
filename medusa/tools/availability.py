"""Tool availability — map tools to their required binaries and check PATH.

Used by `get_tool_catalog()` so the system prompt only advertises tools that
will actually work, and lists the missing ones with install hints.
"""
from __future__ import annotations

import importlib.util
import shutil

from medusa.modules.loader import get_loaded_modules

_INSTALL_HINTS = {
    "nmap": "brew install nmap                    # or: apt install nmap",
    "gobuster": "brew install gobuster              # or: apt install gobuster",
    "feroxbuster": "brew install feroxbuster          # or: cargo install feroxbuster",
    "john": "brew install john                   # or: apt install john",
    "sqlmap": "pip install sqlmap                 # or: brew install sqlmap",
    "hydra": "brew install hydra                  # or: apt install hydra",
    "amass": "brew install amass                  # or: go install github.com/owasp-amass/amass/v4/...@master",
    "subfinder": "brew install subfinder             # or: go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest",
    "httpx": "brew install httpx                  # or: go install github.com/projectdiscovery/httpx/cmd/httpx@latest",
    "nuclei": "brew install nuclei                 # or: go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest",
    "katana": "go install github.com/projectdiscovery/katana/cmd/katana@latest",
    "nikto": "brew install nikto                  # or: apt install nikto",
    "whatweb": "brew install whatweb               # or: apt install whatweb",
    "sslscan": "brew install sslscan               # or: apt install sslscan",
    "ffuf": "brew install ffuf                   # or: go install github.com/ffuf/ffuf/v2@latest",
    "msfconsole": "Install Metasploit: https://www.metasploit.com/",
    "curl": "curl is built into macOS/Linux",
    "gvm": "Install Greenbone: https://greenbone.github.io/docs/latest/",
}


def tool_dependencies() -> dict[str, list[str]]:
    """Map every module tool name to the binaries its manifest declares."""
    deps: dict[str, list[str]] = {}
    for _mod_name, mod_data in get_loaded_modules().items():
        manifest = mod_data.get("manifest", {})
        declared = list(manifest.get("dependencies") or [])
        for tool_name in (manifest.get("tools") or {}):
            deps[tool_name] = declared
    return deps


def _dependency_available(dep: str) -> bool:
    """A declared dependency is satisfied by a binary on PATH or a Python package."""
    if shutil.which(dep):
        return True
    try:
        return importlib.util.find_spec(dep) is not None
    except Exception:
        return False


def binary_status() -> dict[str, bool]:
    """All declared dependency names -> available (binary or Python package)?"""
    seen: dict[str, bool] = {}
    for declared in tool_dependencies().values():
        for dep in declared:
            if dep not in seen:
                seen[dep] = _dependency_available(dep)
    return seen


def missing_binaries() -> dict[str, list[str]]:
    """Tool name -> list of its dependencies that are unavailable."""
    status = binary_status()
    out: dict[str, list[str]] = {}
    for tool, declared in tool_dependencies().items():
        missing = [b for b in declared if not status.get(b, False)]
        if missing:
            out[tool] = missing
    return out


def unavailable_tool_names() -> set[str]:
    """Tools that cannot run right now because a required dependency is missing."""
    return set(missing_binaries())


def install_hint(binary: str) -> str:
    if binary in _INSTALL_HINTS:
        return _INSTALL_HINTS[binary]
    # Unknown names are most likely Python packages
    return f"pip install {binary}"


def startup_banner() -> str | None:
    """A short warning to print at launch when tools are unavailable.

    Returns None when everything a tool needs is present.
    """
    missing = missing_binaries()
    if not missing:
        return None
    lines = [f"{len(missing)} tool(s) unavailable (missing dependencies):"]
    for tool, deps in sorted(missing.items())[:6]:
        lines.append(f"  - {tool}: {', '.join(deps)}")
    lines.append("  Run 'medusa doctor' for install hints.")
    return "\n".join(lines)
