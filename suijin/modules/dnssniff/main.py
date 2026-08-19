import socket


def resolve_host(host: str = "") -> str:
    if not host:
        return "Error: host required"
    try:
        infos = socket.getaddrinfo(host.strip(), None)
        addrs = sorted({i[4][0] for i in infos})
        fam = "v6" if any(":" in a for a in addrs) else ""
        return f"{host} -> {', '.join(addrs)} {fam}".strip()
    except socket.gaierror as e:
        return f"Resolution failed: {e}"


def reverse_dns(ip: str = "") -> str:
    if not ip:
        return "Error: ip required"
    try:
        name = socket.gethostbyaddr(ip.strip())[0]
        fwd = socket.gethostbyname(name)
        match = " (forward-confirmed)" if fwd == ip.strip() else f" (FCrDNS mismatch: {name} -> {fwd})"
        return f"{ip} -> {name}{match}"
    except (socket.herror, socket.gaierror) as e:
        return f"No PTR / lookup failed: {e}"
