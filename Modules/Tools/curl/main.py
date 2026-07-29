"""cURL wrapper."""
import subprocess, shlex
def curl_request(url, method="GET", data="", headers=""):
    if not url: return "Error: url required"
    cmd = f"curl -s -i -X {shlex.quote(method)} {headers} {shlex.quote(url)}"
    if data: cmd += f" -d {shlex.quote(data)}"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, cwd="/tmp")
    return r.stdout or r.stderr or "(no output)"
