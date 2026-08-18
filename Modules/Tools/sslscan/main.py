"""SSLScan wrapper."""
import shlex

from suijin.tools.result import run_command
def sslscan_check(target):
    if not target: return "Error: target required"
    cmd = f"sslscan {shlex.quote(target)} --no-color"
    return run_command(cmd, shell=True, timeout=60, cwd="/tmp", command_text=cmd).format()
