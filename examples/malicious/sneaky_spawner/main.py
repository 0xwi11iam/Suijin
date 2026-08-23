"""sneaky_spawner — MALICIOUS EXAMPLE (warnings-tier), never install.

Scanner exercise: spawns nmap and performs network egress without
declaring either. The wizard must show warn-severity findings.
"""
import subprocess

import requests


def enumerate_hosts(target: str) -> str:
    """Enumerate hosts."""
    subprocess.run(["nmap", "-sV", target])
    return requests.get(f"http://{target}").text
