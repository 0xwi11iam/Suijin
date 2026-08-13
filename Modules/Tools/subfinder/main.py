"""Subfinder wrapper."""
import shlex

from medusa.tools.result import run_command
def subfinder_enum(domain, flags="-all -silent"):
    if not domain: return "Error: domain required"
    cmd = f"subfinder -d {shlex.quote(domain)} {flags}"
    return run_command(cmd, shell=True, timeout=120, cwd="/tmp", command_text=cmd).format()