"""Katana crawler wrapper."""
import subprocess, shlex
def katana_crawl(url, depth=3, flags="-js-crawl -silent"):
    if not url: return "Error: url required"
    cmd = f"katana -u {shlex.quote(url)} -d {int(depth)} {flags}"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120, cwd="/tmp")
    return r.stdout or r.stderr or "(no output)"