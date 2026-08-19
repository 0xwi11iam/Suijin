"""Nuclei vulnerability scanner wrapper."""

import shlex

from suijin.modules.tools.lib.result import run_command


def nuclei_scan(target, templates="cve,exposures,misconfig", flags="-silent -stats"):
    if not target:
        return "Error: target required"
    cmd = f"nuclei -u {shlex.quote(target)} -t {shlex.quote(templates)} {flags}"
    return run_command(cmd, shell=True, timeout=300, cwd="/tmp", command_text=cmd).format()
