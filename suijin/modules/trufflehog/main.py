"""TruffleHog secret scanner wrapper."""

import subprocess, shlex


def trufflehog_scan(target, flags="--only-verified"):
    if not target:
        return "Error: target required"
    cmd = f"trufflehog filesystem {shlex.quote(target)} {flags} --no-update"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300, cwd="/tmp")
    return r.stdout or r.stderr or "(no secrets found)"
