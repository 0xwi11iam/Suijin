"""IPTables control — firewall rule management."""

import subprocess


def iptables_block(ip):
    return (
        subprocess.run(
            ["sudo", "iptables", "-A", "INPUT", "-s", ip, "-j", "DROP"], capture_output=True, text=True
        ).stdout
        or f"Blocked {ip}"
    )


def iptables_unblock(ip):
    return (
        subprocess.run(
            ["sudo", "iptables", "-D", "INPUT", "-s", ip, "-j", "DROP"], capture_output=True, text=True
        ).stdout
        or f"Unblocked {ip}"
    )


def iptables_list():
    return subprocess.run(["sudo", "iptables", "-L", "INPUT", "-n"], capture_output=True, text=True).stdout[:3000]
