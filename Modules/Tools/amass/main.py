"""Amass wrapper."""
import shlex

from suijin.modules.tools.lib.result import run_command
def amass_enum(domain, flags="-passive"):
    if not domain: return "Error: domain required"
    cmd = f"amass enum {flags} -d {shlex.quote(domain)}"
    return run_command(cmd, shell=True, timeout=300, cwd="/tmp", command_text=cmd).format()
