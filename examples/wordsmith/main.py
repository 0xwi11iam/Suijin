"""Wordsmith — tiny text-mangling helpers (example .sja addon).

Zero boilerplate: this bare main.py IS the addon. Public functions become
agent tools; first docstring line is the description.
"""


def wordcount(text: str = "") -> str:
    """Count words, lines, and characters in a text blob."""
    words = len((text or "").split())
    lines = len((text or "").splitlines()) or (1 if text else 0)
    return f"words={words} lines={lines} chars={len(text or '')}"


def leetify(text: str = "", level: int = 1) -> str:
    """Leet-ify text for payload wordlist mutations (level 1-3)."""
    level = max(1, min(3, int(level or 1)))
    table = {"a": "4", "e": "3", "i": "1", "o": "0", "s": "5", "t": "7"}
    out = []
    for ch in str(text or ""):
        low = ch.lower()
        if low in table and (level >= 2 or low in "aeio"):
            out.append(table[low] if level < 3 or low not in "ei" else ch.upper())
        else:
            out.append(ch)
    return "".join(out)
