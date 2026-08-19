import urllib.parse


def url_parse(url: str = "") -> str:
    if not url:
        return "Error: url required"
    u = url.strip()
    p = urllib.parse.urlsplit(u if "://" in u else "http://" + u)
    out = [
        f"scheme={p.scheme} host={p.hostname} port={p.port or ('443' if p.scheme == 'https' else '80')}",
        f"path={p.path}",
        f"query={p.query or '-'}",
        f"fragment={p.fragment or '-'}",
    ]
    flags = []
    low = u.lower()
    if "@" in (p.netloc or ""):
        flags.append("userinfo @ in authority (auth-bypass trick candidate)")
    if p.hostname and (p.hostname == "0x7f000001" or p.hostname.endswith(".0x7f000001")):
        flags.append("hex-encoded localhost (SSRF filter evasion)")
    if "\x00" in u or "%00" in low:
        flags.append("null byte present")
    if u.count("..") > 3 or "%2e%2e" in low:
        flags.append("heavy traversal encoding")
    if "javascript:" in low or "data:" in low:
        flags.append("scheme injection string")
    return "\n".join(out + (["flags:\n  " + "\n  ".join(flags)] if flags else []))


def param_table(url: str = "") -> str:
    if not url:
        return "Error: url required"
    p = urllib.parse.urlsplit(url.strip() if "://" in url.strip() else "http://" + url.strip())
    qs = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    if not qs:
        return "No query parameters."
    rows = []
    for k, v in qs:
        decoded = urllib.parse.unquote_plus(v)
        kind = "empty" if not v else ("int" if v.isdigit() else ("encoded" if decoded != v else "str"))
        rows.append(f"  {k:24} = {decoded[:60]!r:64} [{kind}]")
    return f"{len(qs)} params:\n" + "\n".join(rows)
