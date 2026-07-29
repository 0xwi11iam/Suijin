"""Nikto wrapper."""
import subprocess, shlex
def nikto_scan(url):
    if not url: return "Error: url required"
    cmd = f"nikto -h {shlex.quote(url)} -no404 -nointeractive"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300, cwd="/tmp")
    return r.stdout or r.stderr or "(no output)"
