import subprocess


def snmp_walk(host: str = "", community: str = "public", oid: str = "") -> str:
    if not host:
        return "Error: host required"
    argv = ["snmpwalk", "-v", "2c", "-c", (community or "public").strip(), "-On", "-t", "3", "-r", "2", host.strip()]
    if oid:
        argv.append(oid.strip())
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=240)
    except FileNotFoundError:
        return "Error: snmpwalk not installed"
    except subprocess.TimeoutExpired:
        return "Error: timed out after 240s"
    return f"exit={r.returncode}\n" + ((r.stdout or "") + (r.stderr or ""))[:12000]
