import subprocess


def medusa_brute(host: str = "", service: str = "", users: str = "", passwords: str = "") -> str:
    for k, v in (("host", host), ("service", service), ("users", users), ("passwords", passwords)):
        if not v:
            return f"Error: {k} required"
    argv = [
        "medusa",
        "-h",
        host.strip(),
        "-M",
        service.strip(),
        "-u" if " " not in users else "-U",
        users.strip(),
        "-p" if " " not in passwords else "-P",
        passwords.strip(),
        "-t",
        "4",
        "-f",
    ]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=900)
    except FileNotFoundError:
        return "Error: medusa not installed (hydra pack is the alternative)"
    except subprocess.TimeoutExpired:
        return "Error: timed out after 900s"
    return (
        f"exit={r.returncode}\n"
        + ((r.stdout or "") + (r.stderr or ""))[:8000]
        + "\nAuthorized targets + agreed rate limits only."
    )
