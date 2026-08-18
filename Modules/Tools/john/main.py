"""John the Ripper wrapper."""
import shlex

from suijin.tools.result import run_command


def john_crack(hashfile, wordlist="", format=""):
    if not hashfile:
        return "Error: hashfile required"
    fmt = f"--format={format}" if format else ""
    wl = f"--wordlist={shlex.quote(wordlist)}" if wordlist else ""
    cmd = f"john {shlex.quote(hashfile)} {fmt} {wl}"
    first = run_command(cmd, shell=True, timeout=120, cwd="/tmp", command_text=cmd)
    show_cmd = f"john --show {shlex.quote(hashfile)}"
    second = run_command(show_cmd, shell=True, timeout=10, cwd="/tmp", command_text=show_cmd)
    return first.format() + "\n---\n" + second.format()
