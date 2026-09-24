"""Tool availability — map tools to their required binaries and check PATH.

Used by `get_tool_catalog()` so the system prompt only advertises tools that
will actually work, and by `suijin doctor` for the full dependency sweep
with OS-tailored install commands.
"""

from __future__ import annotations

import importlib.util
import platform
import shutil
import sys


def _os_release_text() -> str:
    try:
        with open("/etc/os-release") as f:
            return f.read().lower()
    except OSError:
        return ""


def detect_package_manager() -> str:
    """The operator's native installer: brew (macOS), apt/dnf/pacman/apk
    (Linux, via os-release), pip (fallback / Windows)."""
    system = platform.system()
    if system == "Darwin":
        return "brew"
    if system == "Linux":
        rel = _os_release_text()
        if any(k in rel for k in ("arch", "manjaro", "endeavour", "cachyos")):
            return "pacman"
        if any(k in rel for k in ("fedora", "rhel", "centos", "amzn", "rocky")):
            return "dnf"
        if "alpine" in rel:
            return "apk"
        return "apt"
    return "pip"


# Install commands per binary: brew/apt are adapted to dnf/pacman/apk
# automatically; "pip"/"go" are alternates; "note" is a last-resort hint.
_INSTALL = {
    # ── network & scanning ────────────────────────────────────────────
    "nmap": {"brew": "brew install nmap", "apt": "sudo apt install nmap"},
    "gobuster": {"brew": "brew install gobuster", "apt": "sudo apt install gobuster"},
    "feroxbuster": {"brew": "brew install feroxbuster", "apt": "sudo apt install feroxbuster"},
    "masscan": {"brew": "brew install masscan", "apt": "sudo apt install masscan"},
    "nikto": {"brew": "brew install nikto", "apt": "sudo apt install nikto"},
    "whatweb": {"brew": "brew install whatweb", "apt": "sudo apt install whatweb"},
    "sslscan": {"brew": "brew install sslscan", "apt": "sudo apt install sslscan"},
    "httpx": {  # projectdiscovery probe — NOT any same-named python script
        "brew": "brew install httpx",
        "apt": "sudo apt install httpx",
        "go": "go install github.com/projectdiscovery/httpx/cmd/httpx@latest",
    },
    "subfinder": {
        "brew": "brew install subfinder",
        "go": "go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest",
    },
    "nuclei": {
        "brew": "brew install nuclei",
        "go": "go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest",
    },
    "katana": {"go": "go install github.com/projectdiscovery/katana/cmd/katana@latest"},
    "amass": {"brew": "brew install amass", "apt": "sudo apt install amass"},
    "ffuf": {"brew": "brew install ffuf", "go": "go install github.com/ffuf/ffuf/v2@latest"},
    "dnsrecon": {"pip": "pip install dnsrecon", "apt": "sudo apt install dnsrecon"},
    # ── exploitation ──────────────────────────────────────────────────
    "sqlmap": {"brew": "brew install sqlmap", "pip": "pip install sqlmap", "apt": "sudo apt install sqlmap"},
    "msfconsole": {"brew": "brew install metasploit", "note": "~700MB · preinstalled on Kali"},
    "metasploit": {"brew": "brew install metasploit", "note": "~700MB · preinstalled on Kali"},
    "metasploit-framework": {"apt": "sudo apt install metasploit-framework", "brew": "brew install metasploit"},
    "crackmapexec": {
        "pip": "pipx install git+https://github.com/Pennyw0rth/NetExec  # then: alias crackmapexec='nxc'",
        "apt": "sudo apt install crackmapexec",
    },
    "medusa": {"brew": "brew install medusa", "apt": "sudo apt install medusa"},
    "searchsploit": {"brew": "brew install exploitdb", "apt": "sudo apt install exploitdb"},
    # ── credentials & cracking ────────────────────────────────────────
    "john": {"brew": "brew install john", "apt": "sudo apt install john"},
    "hashcat": {"brew": "brew install hashcat", "apt": "sudo apt install hashcat"},
    "hydra": {"brew": "brew install hydra", "apt": "sudo apt install hydra"},
    "trufflehog": {
        "brew": "brew install trufflehog",
        "go": "go install github.com/trufflesecurity/trufflehog/v3@latest",
    },
    # ── wireless ──────────────────────────────────────────────────────
    "aircrack-ng": {"brew": "brew install aircrack-ng", "apt": "sudo apt install aircrack-ng"},
    "airodump-ng": {"note": "ships with aircrack-ng (brew install aircrack-ng / sudo apt install aircrack-ng)"},
    # ── web & infra utilities ─────────────────────────────────────────
    "curl": {"note": "built into macOS and Linux"},
    "dig": {"brew": "brew install bind", "apt": "sudo apt install bind9-dnsutils", "note": "built into macOS"},
    "socat": {"brew": "brew install socat", "apt": "sudo apt install socat"},
    "smbclient": {"brew": "brew install samba", "apt": "sudo apt install smbclient"},
    "snmpwalk": {"brew": "brew install net-snmp", "apt": "sudo apt install snmp", "note": "built into macOS"},
    "redis-cli": {"brew": "brew install redis", "apt": "sudo apt install redis-tools"},
    "wafw00f": {"pip": "pip install wafw00f", "apt": "sudo apt install wafw00f"},
    "dirsearch": {"pip": "pip install dirsearch", "apt": "sudo apt install dirsearch"},
    "testssl.sh": {"apt": "sudo apt install testssl.sh", "note": "git clone https://github.com/drwetter/testssl.sh"},
    "mitmproxy": {"brew": "brew install mitmproxy", "pip": "pip install mitmproxy"},
    "playwright": {"pip": "pip install playwright && playwright install"},
    # ── cloud CLIs ────────────────────────────────────────────────────
    "aws": {"brew": "brew install awscli", "pip": "pip install awscli"},
    "gcloud": {"brew": "brew install --cask google-cloud-sdk", "note": "https://cloud.google.com/sdk/docs/install"},
    "az": {"brew": "brew install azure-cli", "note": "https://learn.microsoft.com/cli/azure/install-azure-cli"},
    # ── python packages declared by packs ─────────────────────────────
    "duckduckgo-search": {"pip": "pip install duckduckgo-search"},
    "impacket": {"pip": "pip install impacket"},
    "impacket-secretsdump": {"pip": "pip install impacket"},
    "websocket-client": {"pip": "pip install websocket-client"},
    "neo4j": {
        "brew": "brew install neo4j",
        "pip": "pip install neo4j",
        "note": "optional KG backend — https://neo4j.com",
    },
    "gvm": {"pip": "pip install gvm-tools"},
    "gvm-tools": {"pip": "pip install gvm-tools"},
    "python-gvm": {"pip": "pip install gvm-tools (bundles python-gvm)"},
    "requests": {"pip": "pip install requests"},
}


def _adapt(cmd: str, pm: str) -> str:
    """Translate an apt line for non-debian families (dnf/pacman/apk)."""
    if not cmd or pm in ("apt", "brew", "pip") or not cmd.startswith("sudo apt"):
        return cmd
    return cmd.replace("sudo apt", f"sudo {pm}", 1)


def _env_pip(cmd: str) -> str:
    """pip hints must land in the interpreter running doctor. Machines
    routinely carry several pythons (system, venvs, pipx) and a bare
    `pip install` targets whichever one is first on PATH — the dep then
    reads missing forever despite a 'successful' install."""
    if cmd.startswith("pip install "):
        return f"{sys.executable} -m {cmd}"
    return cmd


def install_hint(binary: str) -> str:
    """The install command for THIS operator's OS (brew/apt/dnf/pacman/pip).
    Falls back to alternates — macOS never gets an apt line while a note
    or brew alternative exists."""
    pm = detect_package_manager()
    entry = _INSTALL.get(binary)
    if entry:
        order = [pm, "pip", "go"] + (["note", "apt"] if pm == "brew" else ["apt", "note"])
        for key in order:
            if entry.get(key):
                return _env_pip(_adapt(entry[key], pm))
        return str(binary)
    return f"{pm} install {binary}   # or: pip install {binary} if it's a Python package"


def tool_dependencies() -> dict[str, list[str]]:
    """Map every module tool name to the binaries its manifest declares."""
    deps: dict[str, list[str]] = {}
    from suijin.modules.loader import get_loaded_modules

    for _mod_name, mod_data in get_loaded_modules().items():
        manifest = mod_data.get("manifest", {})
        declared = list(manifest.get("dependencies") or [])
        for tool_name in manifest.get("tools") or {}:
            deps[tool_name] = declared
    return deps


# dep NAME (as declared in manifests) -> the BINARY actually shipped.
# brew install metasploit pours msfconsole — which("metasploit") is never
# true even with the framework fully installed.
_WHICH_ALIASES = {
    "metasploit": "msfconsole",
    "metasploit-framework": "msfconsole",
}

# pip distribution name -> import name (a declared dep like
# "duckduckgo-search" imports as duckduckgo_search — find_spec on the
# pip name wrongly reported installed packages as missing)
_IMPORT_ALIASES = {
    "duckduckgo-search": "duckduckgo_search",
    "websocket-client": "websocket",
    "gvm-tools": "gvmtools",
    "python-gvm": "gvm",
    "impacket-secretsdump": "impacket",  # script shipped by the impacket dist
}


def _dependency_available(dep: str) -> bool:
    """A declared dependency is satisfied by a binary on PATH or a Python package."""
    if shutil.which(_WHICH_ALIASES.get(dep, dep)):
        return True
    name = _IMPORT_ALIASES.get(dep, dep)
    try:
        return importlib.util.find_spec(name) is not None
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
    lines.append("  Run 'suijin doctor' for install hints.")
    return "\n".join(lines)
