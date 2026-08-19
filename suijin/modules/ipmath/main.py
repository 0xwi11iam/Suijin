import ipaddress


def cidr_expand(cidr: str = "") -> str:
    if not cidr:
        return "Error: cidr required"
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        return f"Error: {e}"
    if net.num_addresses > 4096:
        return f"{net} has {net.num_addresses:,} addresses (cap 4096); net={net.network_address}, bcast={net.broadcast_address}"
    addrs = [str(a) for a in net.hosts()] if net.num_addresses > 2 else [str(a) for a in net]
    return f"{net} ({net.num_addresses} addrs):\n" + "\n".join(addrs)


def ip_info(ip: str = "") -> str:
    if not ip:
        return "Error: ip required"
    try:
        a = ipaddress.ip_address(ip)
    except ValueError as e:
        return f"Error: {e}"
    cls = "PRIVATE" if a.is_private else "PUBLIC"
    if a.is_loopback:
        cls = "LOOPBACK"
    elif a.is_link_local:
        cls = "LINK-LOCAL (169.254/16 — DHCP failure or SSRF target)"
    elif a.is_reserved:
        cls = "RESERVED"
    elif a.is_multicast:
        cls = "MULTICAST"
    return f"{a} -> {cls} | version {a.version} | int {int(a)}" + (
        f" | v6 scope: {a.scope_id}" if a.version == 6 and hasattr(a, "scope_id") else ""
    )
