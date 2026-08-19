import math
import re
from pathlib import Path

_RULES = [
    ("AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}\b")),
    ("Stripe key", re.compile(r"\b[sr]k_live_[0-9a-zA-Z]{24,}\b")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    (
        "Generic assign",
        re.compile(r"(?i)(api[_-]?key|secret|passwd|password|token)\s*[:=]\s*['\"]?([\w./+-]{12,})['\"]?"),
    ),
    ("conn string", re.compile(r"(?i)(postgres|mysql|mongodb(\+srv)?|redis|amqp)://\S{8,}")),
]


def scan_secrets(text: str = "", file: str = "") -> str:
    if file:
        p = Path(file).expanduser()
        if not p.is_file():
            return f"Error: {p} not found"
        text = p.read_text(encoding="utf-8", errors="ignore")
    if not text:
        return "Error: text or file required"
    hits = []
    for label, rx in _RULES:
        for m in rx.finditer(text):
            frag = m.group(0)[:60]
            line = text.count("\n", 0, m.start()) + 1
            hits.append(f"{label} @line {line}: {frag}")
    if not hits:
        return "No credential patterns found."
    uniq = list(dict.fromkeys(hits))
    return f"{len(uniq)} finding(s):\n" + "\n".join(uniq[:40])


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    freq = {c: s.count(c) for c in set(s)}
    n = len(s)
    return -sum((v / n) * math.log2(v / n) for v in freq.values())


def entropy_check(text: str = "", min_len: int = 20) -> str:
    if not text:
        return "Error: text required"
    ml = max(8, int(min_len or 20))
    cands = re.findall(rf"\b[A-Za-z0-9+/=_-]{{{ml},}}\b", text)
    scored = sorted({(_entropy(c), c) for c in cands}, reverse=True)[:15]
    good = [(round(e, 2), c) for e, c in scored if e >= 3.5]
    if not good:
        return f"No strings >= 3.5 bits/char entropy (len>={ml})"
    return "High-entropy candidates (verify before reporting):\n" + "\n".join(f"  {e}  {c[:64]}" for e, c in good)
