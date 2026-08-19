import re

_PATTERNS = {
    "emails": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"),
    "ipv4": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "urls": re.compile(r"https?://[^\s'\"<>)]+"),
    "md5": re.compile(r"\b[0-9a-f]{32}\b", re.I),
    "sha1": re.compile(r"\b[0-9a-f]{40}\b", re.I),
    "sha256": re.compile(r"\b[0-9a-f]{64}\b", re.I),
    "aws_keys": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_tokens": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "private_key_blocks": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "internal_paths": re.compile(r"(?:/[a-z_-]+){2,}\.[a-z]{2,4}\b"),
}


def extract_artifacts(text: str = "") -> str:
    if not text:
        return "Error: text required"
    out = []
    for name, pat in _PATTERNS.items():
        found = sorted(set(pat.findall(text)))[:25]
        if found:
            out.append(f"{name} ({len(found)}):\n  " + "\n  ".join(found))
    return "\n".join(out) if out else "No known artifact patterns found in the text."
