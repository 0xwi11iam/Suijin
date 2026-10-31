"""Environment awareness — the agent KNOWS where it runs (2026-10-31).

The static prompt claimed 'NOT in a container' unconditionally — inside
a box that was a lie, and the agent fought box reality with host
assumptions (/tmp semantics, mounted paths, package managers). Detection
is a capability CHECK (container markers), never a platform guess; the
environment contract renders per-truth.
"""

from __future__ import annotations

import os
from pathlib import Path


def in_box() -> bool:
    """True when running inside a container (the operator's box). Marker
    based — never a uname/platform guess."""
    try:
        if Path("/.dockerenv").is_file() or Path("/opt/suijin-ws").is_dir():
            return True
        if os.environ.get("SUIJIN_BOX"):
            return True
        with open("/proc/1/cgroup", encoding="utf-8", errors="ignore") as fh:
            body = fh.read()
        if "docker" in body or "containerd" in body or "kubepods" in body:
            return True
    except OSError:
        pass
    return False


def box_environment_contract() -> str:
    """The contract rendered when in_box() — replaces the host claims."""
    return (
        "### ENVIRONMENT — you are INSIDE A BOX (container)\n"
        "You run in the operator's container: full freedom, box rules.\n"
        "- This is YOUR machine for the engagement — install packages, write anywhere\n"
        "  the filesystem allows, run services; you are not a guest here.\n"
        "- `/tmp` is the BOX's own tmp (ephemeral, yours for the run) — host /tmp\n"
        "  semantics do NOT apply; no auto-rewrites, no cross-engagement sharing.\n"
        "- HOST paths (e.g. /Users/…) exist only where mounted — probe with ls before\n"
        "  trusting a host path; missing mounts are normal, not errors.\n"
        "- The package manager is the box's (apt on kali) — `pip install` and\n"
        "  `apt-get install` work directly; no sudo dance needed for root.\n"
        "- If a tool needs the HOST (browser login, keychain), it routes through\n"
        "  request_auth — the browser opens on the host and the session flows to you.\n" + _GENERAL_TAIL
    )


def host_environment_contract() -> str:
    """The contract rendered on the host (local_access era, 2026-10-30)."""
    return (
        "### ENVIRONMENT — the host (operator's machine, local access ON)\n"
        "You run as the operator's own process on their machine.\n"
        "- LOCAL ACCESS: absolute paths are REAL — reads and writes land where you\n"
        "  point them. The system floor (/System /Library /etc /usr /bin /var/db)\n"
        "  is the only hard denial.\n"
        "- `$HOME` is the real home under local_access; a bare `/tmp/foo` is\n"
        "  auto-rewritten to your engagement tmp; `/var/tmp` is denied BY DESIGN.\n"
        "- READS and the NETWORK are unrestricted — you probe targets for a living.\n"
        "- The real `/tmp` is machine-shared with other engagements: never treat\n"
        "  files there as yours; verify any /tmp-sourced secret appears on YOUR\n"
        "  target before claiming it.\n" + _GENERAL_TAIL
    )


_GENERAL_TAIL = (
    "- Long jobs: pass `background=true` and poll `job_output` — never block\n"
    "  a turn waiting on a command.\n"
    "- `nmap` flags: `-sV -sC -T4` is a good fast scan. `-p-` scans all 65535 ports (slow).\n"
    "  `--min-rate 1000` speeds up scans. `-oN file.txt` saves output.\n"
    "- `gobuster dir -u URL -w WORDLIST` for directory bruteforce.\n"
    "- `ffuf -u URL/FUZZ -w WORDLIST` for fuzzing.\n"
    "- `curl -I URL` for headers. `curl -v URL` for verbose.\n"
    "- `sqlmap -u URL --batch --random-agent` for SQL injection.\n"
)


def render_environment_contract() -> str:
    """The truth-correct contract: box when in_box(), host otherwise."""
    return box_environment_contract() if in_box() else host_environment_contract()
