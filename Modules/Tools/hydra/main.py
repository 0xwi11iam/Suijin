"""Hydra wrapper."""
import shlex

from suijin.tools.result import run_command
def hydra_brute(target, service, options=""):
    if not target or not service: return "Error: target and service required"
    cmd = f"hydra -I {options} {shlex.quote(target)} {service}"
    return run_command(cmd, shell=True, timeout=600, cwd="/tmp", command_text=cmd).format()
