import subprocess


def dnsrecon_run(domain: str = "") -> str:
    if not domain:
        return "Error: domain required"
    argv = ["dnsrecon", "-d", domain.strip(), "-t", "std,axfr"]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return "Error: dnsrecon not installed"
    except subprocess.TimeoutExpired:
        return "Error: dnsrecon timed out after 300s"
    return f"exit={r.returncode}\n" + ((r.stdout or "") + (r.stderr or ""))[:12000]
