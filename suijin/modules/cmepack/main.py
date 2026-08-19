import subprocess


def cme_smb(target: str = "") -> str:
    if not target:
        return "Error: target required"
    argv = ["crackmapexec", "smb", target.strip(), "-u", "", "-p", "", "--shares"]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return "Error: crackmapexec not installed (netexec is the successor — same CLI)"
    except subprocess.TimeoutExpired:
        return "Error: timed out after 300s"
    return (
        f"exit={r.returncode}\n"
        + ((r.stdout or "") + (r.stderr or ""))[:10000]
        + "\nScope discipline: authorized targets only."
    )
