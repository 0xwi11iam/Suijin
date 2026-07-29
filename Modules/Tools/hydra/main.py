"""Hydra wrapper."""
import subprocess, shlex
def hydra_brute(target, service, options=""):
    if not target or not service: return "Error: target and service required"
    cmd = f"hydra -I {options} {shlex.quote(target)} {service}"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600, cwd="/tmp")
    return r.stdout or r.stderr or "(no output)"
