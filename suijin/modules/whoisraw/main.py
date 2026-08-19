import socket

_SERVERS = {
    "default": "whois.iana.org",
    "ip": "whois.arin.net",
}


def whois_lookup(query: str = "", server: str = "") -> str:
    q = (query or "").strip()
    if not q:
        return "Error: query required"
    is_ip = q.replace(".", "").isdigit()
    srv = (server or "").strip() or ("whois.arin.net" if is_ip else None)
    if srv is None:
        # bootstrap: TLD whois via iana referral
        first = _ask(q, _SERVERS["default"])
        ref = _find_server(first)
        if ref:
            deeper = _ask(q, ref)
            return f"[referred to {ref}]\n{deeper[:4000]}"
        return first[:4000]
    return _ask(q, srv)[:4000]


def _find_server(text: str) -> str:
    import re

    m = re.search(r"whois:\s*([\w.-]+)", text or "")
    return m.group(1) if m else ""


def _ask(q: str, server: str) -> str:
    try:
        s = socket.create_connection((server, 43), timeout=8)
        s.sendall((q + "\r\n").encode())
        chunks = []
        while True:
            b = s.recv(4096)
            if not b:
                break
            chunks.append(b)
        s.close()
        return b"".join(chunks).decode("utf-8", "replace")
    except (socket.timeout, OSError) as e:
        return f"Error querying {server}: {e}"
