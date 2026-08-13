"""Feroxbuster wrapper."""
import shlex

from medusa.tools.result import run_command
def feroxbuster_scan(url, wordlist, extensions="", threads=30):
    if not url or not wordlist: return "Error: url and wordlist required"
    ext = f"-x {extensions}" if extensions else ""
    cmd = f"feroxbuster -u {shlex.quote(url)} -w {shlex.quote(wordlist)} {ext} -t {threads} --silent --no-state"
    return run_command(cmd, shell=True, timeout=300, cwd="/tmp", command_text=cmd).format()
