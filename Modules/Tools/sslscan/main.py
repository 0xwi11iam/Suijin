"""SSLScan wrapper."""
import subprocess, shlex
def sslscan_check(target):
    if not target: return "Error: target required"
    cmd = f"sslscan {shlex.quote(target)} --no-color"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60, cwd="/tmp")
    return r.stdout or r.stderr or "(no output)"
