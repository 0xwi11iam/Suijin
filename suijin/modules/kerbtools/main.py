_MODES = {
    "asrep": ("hashcat -m 18200 / john AS-REP", "Kerberos 5 AS-REP etype 23"),
    "tgs": ("hashcat -m 13100 / john krb5tgs", "Kerberos 5 TGS-REP etype 23"),
    "tgs-rc4": ("hashcat -m 13100", "RC4-HMAC (etype 23)"),
    "tgs-aes256": ("hashcat -m 19700", "AES256-CTS-HMAC-SHA1-96"),
    "tgs-aes128": ("hashcat -m 19600", "AES128-CTS-HMAC-SHA1-96"),
    "kirbi": ("kirbi2john / ticket_converter", "Kirbi binary ticket -> convert first"),
}


def kerb_hash_format(hash_str: str = "", type: str = "") -> str:
    h = hash_str.strip()
    if not h:
        return "Error: hash required"
    t = (type or "").lower()
    if not t:
        if h.startswith("$krb5asrep$"):
            t = "asrep"
        elif h.startswith("$krb5tgs$"):
            t = "tgs"
        elif h.lower().endswith(".kirbi") or len(h) < 64 and h.isalnum():
            t = "kirbi"
        else:
            t = "tgs"
    etype = ""
    if "$23" in h:
        etype = "rc4"
    mode = _MODES.get(t) or _MODES.get(f"tgs-{t}", _MODES["tgs"])
    extra = ""
    if "etype" in h:
        import re

        m = re.search(r"etype(\d+)", h.replace(":", ""))
        if m:
            extra = f"\nfound etype {m.group(1)} in string"
    return f"type={t} -> {mode[0]} ({mode[1]}){extra}"


def spn_candidates(domain: str = "") -> str:
    if not domain:
        return "Error: domain required"
    d = domain.strip().lower().strip(".")
    base = d.split(".")[0]
    svc = [
        "sql",
        "sqlserver",
        "mssql",
        "backup",
        "svc",
        "service",
        "app",
        "web",
        "ftp",
        "file",
        "exchange",
        "smtp",
        "http",
        "test",
        "dev",
        "sharepoint",
        "bi",
        "report",
        "ssrs",
        "adfs",
        "ldap",
    ]
    cands = [f"{s}.{base}@{d}" if "." not in s else f"{s}@{d}" for s in svc]
    return (
        "Likely SPN accounts (feed to GetUserSPNs with creds):\n  "
        + "\n  ".join(cands)
        + "\nAlso check: svc_"
        + base
        + ", "
        + base
        + "_svc, changeme, test_"
        + base
    )
