"""HTTPX probe wrapper."""
import subprocess, shlex
def httpx_probe(url, flags="-status-code -title -tech-detect -server -cl -location"):
    if not url: return "Error: url required"
    cmd = f"httpx -u {shlex.quote(url)} {flags} -silent"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60, cwd="/tmp")
    return r.stdout or r.stderr or "(no output)"