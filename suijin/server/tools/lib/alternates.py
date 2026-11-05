"""Alternates — every failure carries the exact next call (wall memo 1c).

'A missing binary includes the alternate command. A denial names the
platform, the rule, and the exact next call.' The model's first failed
call becomes a lesson with a copy-paste answer, not a dead end.
"""

from __future__ import annotations

_ALTERNATES: dict[str, tuple[str, str]] = {
    # binary → (the alternate that works, one-line how)
    "testssl.sh": (
        "echo | openssl s_client -connect HOST:443 -servername HOST 2>/dev/null | openssl x509 -noout -text | head -40",
        "openssl covers cert/SNI/TLS-version basics without the tool",
    ),
    "hashcat": ("john --wordlist=WORDLIST --format=FORMAT HASHFILE", "john covers the same hash families"),
    "dnsrecon": ("dig NS HOST +short && dig ANY HOST +short", "dig covers the record enumeration"),
    "sqlmap": (
        "http_replay(url='...', op='set-query', field='id', value=\"1' OR '1'='1\", compare=true)",
        "http_replay's differential proves SQLi without sqlmap; manual payloads when the diff gate passes",
    ),
    "nikto": ("nmap_scan(target='HOST', flags='-sV --script http-enum,http-headers')", "nmap's http NSE scripts cover the config checks"),
    "feroxbuster": ("ffuf_fuzz(url='http://HOST/FUZZ', wordlist='/root/wordlists/common.txt')", "ffuf is the equivalent fuzzer"),
    "gobuster": ("ffuf_fuzz(url='http://HOST/FUZZ', wordlist='/root/wordlists/common.txt')", "ffuf is the equivalent fuzzer"),
    "katana": ("execute_terminal(cmd='python3 -c \"import requests;r=requests.get(\\\"URL\\\");print(r.text[:5000)\"')", "a fetch + grep covers basic crawl"),
    "impacket-*": ("python3 -c 'from impacket.dcerpc.v5 import ...'", "pip install impacket first: pip_install('impacket')"),
}


def alternate_for(binary: str) -> str | None:
    """The working command for a missing binary, or None."""
    b = str(binary or "").strip()
    if not b:
        return None
    hit = _ALTERNATES.get(b) or _ALTERNATES.get(b.split()[0])
    if not hit:
        return None
    cmd, how = hit
    return f"ALTERNATE (works now): {cmd}\n({how})"


def annotate_missing_binary(tool_output: str, binary_hint: str = "") -> str:
    """Post-process a not-installed error: append the alternate when one
    exists. Wraps any tool result string without changing success paths."""
    t = str(tool_output or "")
    if "not installed" not in t and "not found" not in t and "command not found" not in t:
        return t

    for name in _ALTERNATES:
        if name in t or name in str(binary_hint or ""):
            alt = alternate_for(name)
            if alt:
                return t.rstrip() + "\n\n" + alt
    return t
