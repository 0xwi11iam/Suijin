"""John the Ripper wrapper."""
import subprocess, shlex
def john_crack(hashfile, wordlist="", format=""):
    if not hashfile: return "Error: hashfile required"
    fmt = f"--format={format}" if format else ""
    wl = f"--wordlist={shlex.quote(wordlist)}" if wordlist else ""
    cmd = f"john {shlex.quote(hashfile)} {fmt} {wl}"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120, cwd="/tmp")
    r2 = subprocess.run(f"john --show {shlex.quote(hashfile)}", shell=True, capture_output=True, text=True, timeout=10)
    return f"{{r.stdout}}\n---\n{{r2.stdout}}"
