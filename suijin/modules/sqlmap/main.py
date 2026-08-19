"""SQLMap wrapper."""

import shlex

from suijin.modules.tools.lib.result import run_command


def sqlmap_scan(url, flags="--batch --random-agent", tamper=""):
    if not url:
        return "Error: url required"
    tamper_flag = f"--tamper={tamper}" if tamper else ""
    cmd = f"sqlmap -u {shlex.quote(url)} {flags} {tamper_flag}".strip()
    return run_command(cmd, shell=True, timeout=600, cwd="/tmp", command_text=cmd).format()
