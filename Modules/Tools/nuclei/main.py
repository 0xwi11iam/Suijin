"""Nuclei vulnerability scanner wrapper."""
import subprocess, shlex
def nuclei_scan(target, templates="cve,exposures,misconfig", flags="-silent -stats"):
    if not target: return "Error: target required"
    cmd = f"nuclei -u {shlex.quote(target)} -t {shlex.quote(templates)} {flags}"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300, cwd="/tmp")
    return r.stdout or r.stderr or "(no findings or error)"