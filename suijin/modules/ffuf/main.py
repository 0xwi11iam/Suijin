"""FFUF wrapper."""

import subprocess, shlex


def ffuf_fuzz(url, wordlist, options=""):
    if not url or not wordlist:
        return "Error: url and wordlist required"
    cmd = f"ffuf -u {shlex.quote(url)} -w {shlex.quote(wordlist)} {options} -of csv"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300, cwd="/tmp")
    return r.stdout or r.stderr or "(no output)"
