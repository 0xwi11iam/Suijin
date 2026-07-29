"""Nmap scanner wrapper."""
import subprocess, shlex
def nmap_scan(target, flags="-sV -sC"):
    if not target: return "Error: target required"
    cmd = f"nmap {flags} {shlex.quote(target)}"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300, cwd="/tmp")
    return r.stdout or r.stderr or "(no output)"
