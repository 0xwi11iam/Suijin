"""Feroxbuster wrapper."""
import subprocess, shlex
def feroxbuster_scan(url, wordlist, extensions="", threads=30):
    if not url or not wordlist: return "Error: url and wordlist required"
    ext = f"-x {extensions}" if extensions else ""
    cmd = f"feroxbuster -u {shlex.quote(url)} -w {shlex.quote(wordlist)} {ext} -t {threads} --silent --no-state"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300, cwd="/tmp")
    return r.stdout or r.stderr or "(no output)"
