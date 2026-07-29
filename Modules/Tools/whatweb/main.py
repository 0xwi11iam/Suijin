"""WhatWeb tech fingerprinting wrapper."""
import subprocess, shlex
def whatweb_scan(url, flags="--colour=never --no-errors"):
    if not url: return "Error: url required"
    cmd = f"whatweb {shlex.quote(url)} {flags}"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60, cwd="/tmp")
    return r.stdout or r.stderr or "(no output)"