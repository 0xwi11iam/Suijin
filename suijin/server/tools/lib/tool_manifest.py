"""tool manifest — declarative installation metadata for every tool.

The restructure (2026-10-20, operator directive): tools DECLARE —
  platforms : where they install — ``kali`` · ``linux`` · ``macos``
              (the SAME vocabulary pack manifests already use; ``all``
              is accepted on input)
  prereqs   : runtimes a fallback needs (go · pip · gem) — installed
              BEFORE any tool that needs them, never discovered by
              failure
  install   : an ORDERED FALLBACK CHAIN per platform — try line 1,
              fall through on failure, every line automation-safe
  verify    : how presence is decided — a binary name (``testssl`` for
              ``testssl.sh``) or a python module (``requests`` has no
              binary at all)

Sources, highest precedence first:
  1. pack manifests — an optional ``tool_install`` block lets a pack
     declare install metadata for its dependencies
  2. the hand overrides below
  3. derivation from the doctor's _INSTALL hint table

Consumers: the box provision (probe → plan → prereqs → install),
the doctor (missing-dep hints + platform applicability), `suijin tools
--manifest` (the full declarative table). The raw hint table stays for
human display; this module is the executable contract.
"""

from __future__ import annotations

import contextlib

PLATFORMS = ("kali", "linux", "macos", "windows")

#: runtime prereq -> the package that provides it on debian/kali.
RUNTIME_PACKAGES: dict[str, str] = {"go": "golang-go", "gem": "ruby", "pip": "python3-pip"}

#: python packages whose import name differs from the tool name
_PY_MODULES = {
    "websocket-client": "websocket",
    "duckduckgo-search": "duckduckgo_search",
    "impacket-secretsdump": "impacket",
}

#: hand overrides where derivation from the hint table is wrong or
#: incomplete (platforms the keys don't imply, extra fallbacks).
_OVERRIDES: dict[str, dict] = {
    "nmap": {"platforms": ["kali", "linux", "macos"]},
    "ffuf": {
        "platforms": ["kali", "linux", "macos"],
        "kali": [
            "sudo apt install -y --no-install-recommends ffuf",
            "go install github.com/ffuf/ffuf/v2@latest",
        ],
        "linux": [
            "sudo apt install -y --no-install-recommends ffuf",
            "go install github.com/ffuf/ffuf/v2@latest",
        ],
        "macos": ["brew install ffuf", "go install github.com/ffuf/ffuf/v2@latest"],
    },
    "nuclei": {
        "platforms": ["kali", "linux", "macos"],
        "kali": [
            "sudo apt install -y --no-install-recommends nuclei",
            "go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest",
        ],
        "macos": ["brew install nuclei", "go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest"],
    },
    "subfinder": {
        "platforms": ["kali", "linux", "macos"],
        "kali": [
            "sudo apt install -y --no-install-recommends subfinder",
            "go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest",
        ],
        "macos": ["brew install subfinder", "go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest"],
    },
    "katana": {
        "platforms": ["kali", "linux", "macos"],
        "kali": [
            "sudo apt install -y --no-install-recommends katana",
            "go install github.com/projectdiscovery/katana/cmd/katana@latest",
        ],
        "macos": ["go install github.com/projectdiscovery/katana/cmd/katana@latest"],
    },
    "httpx": {
        "platforms": ["kali", "linux", "macos"],
        "kali": [
            "sudo apt install -y --no-install-recommends httpx",
            "go install github.com/projectdiscovery/httpx/cmd/httpx@latest",
        ],
        "macos": ["brew install httpx", "go install github.com/projectdiscovery/httpx/cmd/httpx@latest"],
    },
    "wpscan": {
        "platforms": ["kali", "linux", "macos"],
        "kali": ["sudo apt install -y --no-install-recommends wpscan", "gem install wpscan"],
        "linux": ["sudo apt install -y --no-install-recommends wpscan", "gem install wpscan"],
        "macos": ["gem install wpscan"],
    },
    "aws": {"platforms": ["kali", "linux", "macos"]},
    "sudo": {"platforms": ["kali", "linux"], "built_in": True},
    # NetExec ships `nxc`; Kali's apt package ships `crackmapexec` — both
    # are valid, so presence accepts either (apt path won the probe)
    "crackmapexec": {"verify": {"kind": "binary", "values": ["nxc", "crackmapexec"]}},
    # Kali's apt dirsearch imports pkg_resources, which setuptools >=81
    # removed and Python 3.14 distros no longer shim — install the compat
    # copy in the user site so the packaged binary actually runs
    "dirsearch": {
        "platforms": ["kali", "linux", "macos"],
        "kali": [
            'sudo apt install -y --no-install-recommends dirsearch && pip install --user --break-system-packages --ignore-installed "setuptools<81"',
        ],
        "linux": ['pip install --user --break-system-packages --ignore-installed "setuptools<81" dirsearch'],
        "macos": ["pip install dirsearch"],
    },
    # Kali packages trufflehog directly; the go module is UNINSTALLABLE
    # (replace directives) so the vendor script is the real fallback
    "trufflehog": {
        "platforms": ["kali", "linux", "macos"],
        "kali": [
            "sudo apt install -y --no-install-recommends trufflehog",
            "go install github.com/trufflesecurity/trufflehog/v3@latest",
            "curl -sSfL https://raw.githubusercontent.com/trufflesecurity/trufflehog/main/scripts/install.sh | sudo sh -s -- -b /usr/local/bin",
        ],
        "linux": [
            "go install github.com/trufflesecurity/trufflehog/v3@latest",
            "curl -sSfL https://raw.githubusercontent.com/trufflesecurity/trufflehog/main/scripts/install.sh | sudo sh -s -- -b /usr/local/bin",
        ],
        "macos": ["brew install trufflehog"],
    },
    # Kali packages azure-cli; Debian gets the vendor apt script
    "az": {
        "platforms": ["kali", "linux", "macos"],
        "kali": [
            "sudo apt install -y --no-install-recommends azure-cli",
            "curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash",
        ],
        "linux": [
            "sudo apt install -y --no-install-recommends azure-cli",
            "curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash",
        ],
        "macos": ["brew install azure-cli"],
    },
    # no distro package — the official linux tarball, installed per-user
    # (the shell profile carries ~/google-cloud-sdk/bin)
    "gcloud": {
        "platforms": ["kali", "linux", "macos"],
        "kali": [
            'ARCH=$(uname -m); case "$ARCH" in aarch64|arm64) SUF=arm;; *) SUF=x86_64;; esac; curl -sSL "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-${SUF}.tar.gz" | tar xz -C "$HOME" && "$HOME/google-cloud-sdk/install.sh" --quiet',
        ],
        "linux": [
            'ARCH=$(uname -m); case "$ARCH" in aarch64|arm64) SUF=arm;; *) SUF=x86_64;; esac; curl -sSL "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-${SUF}.tar.gz" | tar xz -C "$HOME" && "$HOME/google-cloud-sdk/install.sh" --quiet',
        ],
        "macos": ["brew install --cask google-cloud-sdk"],
    },
    # headless box: the shell is what web automation needs, not 1.3G of
    # every browser engine (other engines install on demand)
    "playwright": {
        "platforms": ["kali", "linux", "macos"],
        "kali": ["pip install playwright && python3 -m playwright install chromium --only-shell"],
        "linux": ["pip install playwright && python3 -m playwright install chromium --only-shell"],
        "macos": ["pip install playwright && python3 -m playwright install chromium --only-shell"],
    },
}

#: platform -> the keys of a hint-table entry that imply support
_PLATFORM_KEYS = {
    "macos": ("brew",),
    "linux": ("apt",),
    "kali": ("apt",),
}


def normalize_platform(platform: str) -> str:
    """One vocabulary everywhere: mac→macos, darwin→macos, win→windows."""
    p = str(platform or "").strip().lower()
    if p in ("mac", "macos", "darwin", "osx"):
        return "macos"
    if p in ("win", "windows", "win32"):
        return "windows"
    if p.startswith("kali"):
        return "kali"
    if p.startswith("linux"):
        return "linux"
    return p


def platform_supported(declared: list[str], host: str | None = None) -> bool:
    """Does a pack/tool's declared platform list cover the host?
    ``all`` covers everything; kali is a linux, so ``linux`` covers it."""
    host = normalize_platform(host or host_platform())
    plats = {normalize_platform(p) for p in (declared or [])}
    if not plats or "all" in plats:
        return True
    if host in plats:
        return True
    return host == "kali" and "linux" in plats


def host_platform() -> str:
    """This machine's manifest platform: macos · kali · linux · windows."""
    import sys

    if sys.platform == "darwin":
        return "macos"
    if not sys.platform.startswith("linux"):
        return "windows"
    with contextlib.suppress(Exception), open("/etc/os-release") as fh:  # noqa: PTH123 — fixed system path
        if "kali" in fh.read().lower():
            return "kali"
    return "linux"


def _aptize(line: str) -> str:
    """Automation-safe apt line: -y + --no-install-recommends (the
    human-hint hang: apt without -y waits at an invisible prompt)."""
    if "apt install" not in line:
        return line
    if " -y" not in line and "-y " not in line[: line.index("install") + len("install") + 1]:
        line = line.replace("apt install", "apt install -y", 1)
    if "--no-install-recommends" not in line:
        line = line.replace("apt install", "apt install --no-install-recommends", 1)
    return line


def _line_runtime(line: str) -> str | None:
    low = line.strip().lower()
    if low.startswith("go install"):
        return "go"
    if low.startswith("pip install") or low.startswith("pip3 install") or " -m pip install" in low:
        return "pip"
    if low.startswith("gem install"):
        return "gem"
    return None


def _raw_table() -> dict:
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.availability import _INSTALL

        return dict(_INSTALL)
    return {}


def _which_aliases() -> dict[str, str]:
    """dep name -> the binary actually shipped (metasploit→msfconsole)."""
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.availability import _WHICH_ALIASES

        return dict(_WHICH_ALIASES)
    return {}


def _pack_records() -> dict[str, dict]:
    """Optional ``tool_install`` blocks declared by packs —
    ``{tool: {platforms, prereqs, install, verify}}``. This is how a
    pack ships install metadata for its own dependencies. Read from the
    manifest files directly (no pack import side effects)."""
    out: dict[str, dict] = {}
    with contextlib.suppress(Exception):
        import json
        from pathlib import Path

        from suijin.modules.loader import PACK_ROOTS

        for root in PACK_ROOTS:
            root_p = Path(root)
            if not root_p.is_dir():
                continue
            for mf_path in sorted(root_p.glob("*/manifest.json")):
                with contextlib.suppress(Exception):
                    manifest_data = json.loads(mf_path.read_text(encoding="utf-8"))
                    block = manifest_data.get("tool_install")
                    if not isinstance(block, dict):
                        continue
                    for name, rec in block.items():
                        if isinstance(rec, dict) and str(name).strip():
                            out[str(name).strip()] = rec
    return out


def _override_for(name: str, packed: dict) -> dict:
    """Hand overrides, then pack declarations on top (pack wins)."""
    ovr = dict(_OVERRIDES.get(name) or {})
    ovr.update(packed.get(name) or {})
    return ovr


def _py_module(name: str) -> str:
    return _PY_MODULES.get(name, name.replace("-", "_"))


def _derive_verify(name: str, entry: dict, ovr: dict) -> dict:
    """How to decide presence: a declared verify > a binary alias >
    a python module for pip-only deps (they have no binary at all)."""
    if isinstance(ovr.get("verify"), dict) and ovr["verify"].get("kind"):
        return dict(ovr["verify"])
    if entry.get("pip") and not any(entry.get(k) for k in ("apt", "brew", "go", "gem")):
        return {"kind": "python", "value": _py_module(name)}
    return {"kind": "binary", "value": _which_aliases().get(name, name)}


def _entry_for(name: str, raw: dict, packed: dict) -> dict:
    """One tool's manifest record: platforms, prereqs, install chains.
    Note-only entries (commands live in the note prose) get their
    chains parsed out of the note — the hints are real commands."""
    import re

    entry = raw.get(name) or {}
    ovr = _override_for(name, packed)
    note = str(entry.get("note") or "")
    # built-ins: the note says it ships with the OS — nothing to
    # install, presence checks pass trivially, chains legitimately empty
    built_in = bool(
        re.search(r"built into|preinstalled|ships with (macos|mac|linux|kali)|part of the (os|base)", note, re.I)
    ) and not any(entry.get(k) for k in ("apt", "brew", "pip", "go", "gem"))
    if ovr.get("built_in"):
        built_in = True
    # note-prose commands are real hints: 'brew install aircrack-ng /
    # sudo apt install aircrack-ng' inside a note — parsed per manager
    # so alternatives stay SEPARATE commands (a greedy single pattern
    # merges across the slash)
    note_apt = next(
        (
            f"sudo {m}" if not m.startswith("sudo") else m
            for m in re.findall(r"(?:sudo )?apt(?:-get)? install [-\w.]+", note)
        ),
        None,
    )
    note_brew = next((m for m in re.findall(r"brew install [-\w.]+", note)), None)

    platforms = [normalize_platform(p) for p in (ovr.get("platforms") or [])]
    if not platforms:
        implied = {p for p, keys in _PLATFORM_KEYS.items() for k in keys if entry.get(k)}
        if note_brew:
            implied.add("macos")
        if note_apt:
            implied |= {"kali", "linux"}
        if any(entry.get(k) for k in ("pip", "go", "gem")):
            implied = {"kali", "linux", "macos"}
        if built_in:
            # a built-in with no install evidence: unix-linux by default;
            # only a POSITIVE macOS mention claims macos support
            implied = {"kali", "linux"}
            if re.search(
                r"(built into|ships with|part of).{0,20}macos|macos.{0,20}(built in|preinstalled)", note, re.I
            ):
                implied.add("macos")
        platforms = sorted({normalize_platform(p) for p in implied})
        if not platforms:
            platforms = ["kali", "linux", "macos"]
    platforms = [p for p in PLATFORMS if p in platforms]  # canonical order

    apt = _aptize(entry["apt"]) if entry.get("apt") else (_aptize(note_apt) if note_apt else None)
    brew = entry.get("brew") or note_brew
    generic_apt = f"sudo apt install -y --no-install-recommends {name}"
    generic_brew = f"brew install {name}"
    runtimes = [entry.get(k) for k in ("pip", "go", "gem") if entry.get(k)]

    chains: dict[str, list[str]] = {}
    for plat in PLATFORMS:
        if plat not in platforms:
            continue  # no chain for an undeclared platform — the contract
        if plat == "macos":
            chains[plat] = list(ovr.get("macos") or (([brew] if brew else [generic_brew]) + runtimes))
        elif plat in ("kali", "linux"):
            # ALWAYS include the apt attempt (explicit package line, or
            # the generic package named after the tool) — the batched
            # probe decides, and Kali packages more than the hint table
            # knew (trufflehog, azure-cli, mitmproxy were missed).
            chains[plat] = list(ovr.get(plat) or (([apt] if apt else [generic_apt]) + runtimes))
    _rt: set[str] = set()
    for _chain in chains.values():
        for line in _chain:
            if rt := _line_runtime(line):
                _rt.add(rt)
    rec = {
        "platforms": platforms,
        "prereqs": sorted(_rt),
        "install": chains,
        "verify": _derive_verify(name, entry, ovr),
    }
    if built_in:
        rec["built_in"] = True
        rec["install"] = {}  # nothing to install anywhere — presence is free
    return rec


_MANIFEST_CACHE: dict | None = None


def manifest(refresh: bool = False) -> dict[str, dict]:
    """{tool: {platforms, prereqs, install:{kali,linux,macos}, verify}}."""
    global _MANIFEST_CACHE
    if _MANIFEST_CACHE is not None and not refresh:
        return _MANIFEST_CACHE
    raw = _raw_table()
    packed = _pack_records()
    names = sorted(set(raw) | set(_OVERRIDES) | set(packed))
    _MANIFEST_CACHE = {name: _entry_for(name, raw, packed) for name in names}
    return _MANIFEST_CACHE


def platforms_of(tool: str) -> list[str]:
    return list((manifest().get(tool) or {}).get("platforms") or [])


def prereqs_of(tool: str, platform: str = "kali") -> list[str]:
    """Runtimes this tool's chain may need on a platform — declared,
    not discovered by failure."""
    rec = manifest().get(tool) or {}
    lines = (rec.get("install") or {}).get(normalize_platform(platform)) or []
    return sorted({rt for ln in lines if (rt := _line_runtime(ln))})


def install_plan(tool: str, platform: str = "kali") -> list[str]:
    """The ordered fallback chain — line 1 first, fall through on
    failure. Every line automation-safe. A tool absent from the
    manifest gets the platform's generic attempt so presence decides;
    a KNOWN tool on an undeclared platform gets no plan (honest)."""
    plat = normalize_platform(platform)
    rec = manifest().get(tool) or {}
    chain = list((rec.get("install") or {}).get(plat) or [])
    if chain:
        return chain
    if rec:
        return []  # built-in, or not declared for this platform
    if plat == "macos":
        return [f"brew install {tool}"]
    if plat in ("kali", "linux"):
        return [f"sudo apt install -y --no-install-recommends {tool}"]
    return []


def prereqs_for(tools: list[str], platform: str = "kali") -> set[str]:
    """All runtimes a tool SET may need on a platform — the box installs
    these BEFORE the tools."""
    out: set[str] = set()
    for t in tools:
        out.update(prereqs_of(t, platform))
    return out


def verify_checks(tool: str) -> list[tuple[str, str]]:
    """Every acceptable presence check for a tool — a list because some
    tools ship different binaries per install path (Kali's apt
    crackmapexec vs pipx's nxc)."""
    rec = manifest().get(tool) or {}
    v = rec.get("verify") or {}
    kind = str(v.get("kind") or "binary")
    raw = v.get("values")
    if raw is None:
        raw = [v.get("value") or tool]
    return [(kind, str(x)) for x in raw if str(x)]


def verify_of(tool: str) -> tuple[str, str]:
    """The FIRST declared check — kept for callers that report one."""
    checks = verify_checks(tool)
    return checks[0] if checks else ("binary", tool)


def supports(tool: str, platform: str | None = None) -> bool:
    return platform_supported(platforms_of(tool), platform)
