"""ensure_tools (2026-10-20, CyberStrike adaptation).

Agent-callable, IDEMPOTENT toolchain self-provisioning: check which
named security binaries exist, install only the missing ones via the
platform package manager, never reinstall. Capped per call so a broad
request can't eat the engagement — big installs belong in background.

Curated map only (the doctor remains the full-coverage authority) —
the recon workhorses an engagement actually stalls without.
"""

from __future__ import annotations

import shutil
import subprocess

#: tool -> (macOS install, debian install). Binaries available via pip
#: route through pip instead. Keep curated — this is the "unblock the
#: engagement" list, not the doctor.
_TOOLCHAIN: dict[str, tuple[str, str]] = {
    "nmap": ("brew install nmap", "sudo apt-get install -y nmap"),
    "ffuf": ("brew install ffuf", "go install github.com/ffuf/ffuf/v2@latest"),
    "nuclei": ("brew install nuclei", "go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest"),
    "httpx": ("brew install httpx", "go install github.com/projectdiscovery/httpx/cmd/httpx@latest"),
    "subfinder": ("brew install subfinder", "go install github.com/projectdiscovery/subfinder/v2@latest"),
    "masscan": ("brew install masscan", "sudo apt-get install -y masscan"),
    "sqlmap": ("pip install sqlmap", "pip install sqlmap"),
    "nikto": ("brew install nikto", "sudo apt-get install -y nikto"),
    "testssl": ("brew install testssl-ssl", "sudo apt-get install -y testssl.sh"),
    "wafw00f": ("pip install wafw00f", "pip install wafw00f"),
    "wpscan": ("brew install wpscan", "sudo apt-get install -y wpscan"),
    "gobuster": ("brew install gobuster", "sudo apt-get install -y gobuster"),
    "katana": ("brew install katana", "go install github.com/projectdiscovery/katana/cmd/katana@latest"),
    "dalfox": ("brew install dalfox", "go install github.com/hahwul/dalfox/v2@latest"),
}

_MAX_INSTALLS_PER_CALL = 3


def _which_any(name: str) -> bool:
    """Which-style probe incl. sbin blindspot dirs (the doctor lesson)."""
    import os
    from pathlib import Path

    path_dirs = (os.environ.get("PATH") or "").split(":")
    path_dirs += ["/usr/sbin", "/sbin", "/usr/local/sbin", "/opt/homebrew/bin", "/opt/homebrew/sbin"]
    for d in path_dirs:
        if d and (Path(d) / name).is_file():
            return True
    return shutil.which(name) is not None


def ensure_tools(tools: list | None = None, install: bool = True) -> str:
    """Idempotent toolchain check (+install). tools: names to verify
    (default: the curated list). install: actually install the missing
    ones (max 3 per call — run again for more)."""
    try:
        import sys

        wanted = [str(t).strip().lower() for t in (tools or list(_TOOLCHAIN)) if str(t).strip()]
        unknown = [t for t in wanted if t not in _TOOLCHAIN]
        known = [t for t in wanted if t in _TOOLCHAIN]

        present, missing = [], []
        for t in known:
            (present if _which_any(t) else missing).append(t)

        lines = [f"TOOLCHAIN: {len(present)}/{len(known)} present"]
        if present:
            lines.append("present: " + ", ".join(present))
        if missing:
            lines.append("missing: " + ", ".join(missing))
        if unknown:
            lines.append(f"not in the curated map (doctor has full coverage): {', '.join(unknown)}")

        if not missing or not install:
            if missing:
                lines.append("call again with install=true to install (or use pip_install for pip-map tools)")
            return "\n".join(lines)

        to_install = missing[:_MAX_INSTALLS_PER_CALL]
        skipped = missing[_MAX_INSTALLS_PER_CALL:]
        lines.append(f"installing: {', '.join(to_install)}")
        if skipped:
            lines.append(f"(deferred to a next call: {', '.join(skipped)})")

        import contextlib

        results = []
        for t in to_install:
            mac_cmd, linux_cmd = _TOOLCHAIN[t]
            cmd = mac_cmd if sys.platform == "darwin" else linux_cmd
            full = f"{sys.executable} -m {cmd}" if cmd.startswith("pip ") else cmd
            try:
                with contextlib.suppress(Exception):
                    proc = subprocess.run(
                        ["/bin/sh", "-c", full],
                        capture_output=True,
                        text=True,
                        timeout=420,
                    )
                ok = proc.returncode == 0 or _which_any(t)
                tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-1][:120] if proc else ""
                results.append(f"- {t}: {'INSTALLED' if ok else 'FAILED'}{f' — {tail}' if tail else ''}")
            except subprocess.TimeoutExpired:
                results.append(f"- {t}: TIMEOUT (420s) — install in background or manually: {full}")
            except Exception as e:  # noqa: BLE001
                results.append(f"- {t}: ERROR {e}")
        lines.extend(results)
        lines.append("(verify with ensure_tools again — installs are skip-if-present)")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"
