"""Amass wrapper."""
import subprocess, shlex
def amass_enum(domain, flags="-passive"):
    if not domain: return "Error: domain required"
    cmd = f"amass enum {flags} -d {shlex.quote(domain)}"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300, cwd="/tmp")
    return r.stdout or r.stderr or "(no output)"
