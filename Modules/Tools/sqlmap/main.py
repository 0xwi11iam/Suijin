"""SQLMap wrapper."""
import subprocess, shlex
def sqlmap_scan(url, flags="--batch --random-agent"):
    if not url: return "Error: url required"
    cmd = f"sqlmap -u {shlex.quote(url)} {flags}"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600, cwd="/tmp")
    return r.stdout or r.stderr or "(no output)"
