"""Nmap scanner wrapper."""
import shlex

from suijin.tools.result import run_command


def nmap_scan(target, flags="-sV -sC"):
    if not target:
        return "Error: target required"
    cmd = f"nmap {flags} {shlex.quote(target)}"
    return run_command(cmd, shell=True, timeout=300, cwd="/tmp", command_text=cmd).format()
