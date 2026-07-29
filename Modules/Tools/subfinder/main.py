"""Subfinder wrapper."""
import subprocess, shlex
def subfinder_enum(domain, flags="-all -silent"):
    if not domain: return "Error: domain required"
    cmd = f"subfinder -d {shlex.quote(domain)} {flags}"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120, cwd="/tmp")
    return r.stdout or r.stderr or "(no output)"