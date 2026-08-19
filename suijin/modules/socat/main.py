"""Socat wrapper."""

import subprocess


def socat_relay(cmd):
    if not cmd:
        return "Error: cmd required"
    r = subprocess.run(f"socat {cmd}", shell=True, capture_output=True, text=True, timeout=30, cwd="/tmp")
    return r.stdout or r.stderr or "(no output)"
