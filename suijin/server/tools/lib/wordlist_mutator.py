"""Wordlist mutation + cewl-style extraction — offline password/fuzz fuel.

mutate_wordlist: rule-based transforms of seed words (case, leet, years,
common suffixes/prefixes) into a deduped wordlist file.
cewl_words:      harvest unique words from fetched HTTP responses into a
wordlist (custom wordlists from target content, cewl-style).
"""

from __future__ import annotations

import re


def _ws_dir():
    """Workspace dir (honours a monkeypatched module attr)."""
    v = globals().get("WORKSPACE_DIR")
    if v is not None:
        return v
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR


def _resolve(path):
    fn = globals().get("resolve_workspace_path")
    if fn is not None:
        return fn(path)
    from suijin.modules.platform.lib.workspace import resolve_workspace_path

    return resolve_workspace_path(path)


def __getattr__(name):
    if name == "WORKSPACE_DIR":
        return _ws_dir()
    if name == "resolve_workspace_path":
        return _resolve
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_LEET = str.maketrans("aeiostAEIOST", "431057431057")
_SUFFIXES = ("", "!", "123", "1234", "2024", "2025", "2026", "#", "@", "?", "1", "69", "!!")
_PREFIXES = ("", "!", "admin", "test")
_YEARS = tuple(str(y) for y in range(2015, 2027))
_MAX_OUTPUT = 50_000

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")


def mutate_wordlist(
    seeds: list | None,
    out: str = "wordlists/mutated.txt",
    leet: bool = True,
    years: bool = True,
    suffixes: bool = True,
    max_words: int = _MAX_OUTPUT,
) -> str:
    """Expand seed words into a mutated wordlist at suijin_agent/<out>."""
    seeds = [str(s).strip() for s in (seeds or []) if str(s).strip()]
    if not seeds:
        return "Error: seeds required (e.g. ['field-target', 'DF', 'support@example.com' local-part])."
    max_words = max(10, min(int(max_words), _MAX_OUTPUT))

    seen: set[str] = set()

    def add(w: str):
        if len(seen) < max_words and w and w not in seen:
            seen.add(w)

    for seed in seeds:
        base = seed.split("@")[0]  # email local-part is the interesting bit
        variants = {base, base.lower(), base.upper(), base.capitalize()}
        if leet:
            variants |= {v.translate(_LEET) for v in list(variants)}
        for v in list(variants):
            add(v)
            if suffixes:
                for s in _SUFFIXES:
                    add(v + s)
                    add(v.capitalize() + s)
            if years:
                for y in _YEARS:
                    add(v + y)
        for p in _PREFIXES[1:]:  # non-empty prefixes
            add(p + base.capitalize())
            add(p + base)

    words = sorted(seen)
    target = _resolve(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(words) + "\n")
    try:
        shown = str(target.resolve().relative_to(_ws_dir().resolve()))
    except ValueError:
        shown = str(target)
    return (
        f"mutated {len(seeds)} seed(s) -> {len(words):,} word(s) "
        f"(leet={leet}, years={years}, suffixes={suffixes}) -> {shown}"
    )


_TAG_RE = re.compile(r"<[^>]+>")
_NONWORD_RE = re.compile(r"[^A-Za-z0-9_-]+")


def extract_words(html: str, min_len: int = 3, max_len: int = 24) -> list[str]:
    """Distinct words from HTML text (tags/script/style stripped)."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    text = _TAG_RE.sub(" ", text)
    words = set()
    for w in _WORD_RE.findall(text):
        if min_len <= len(w) <= max_len:
            words.add(w)
    return sorted(words)


def cewl_words(url: str, out: str | None = None, min_len: int = 3, max_len: int = 24, session=None) -> str:
    """Fetch a URL and build a wordlist from its visible words."""
    if not url or not str(url).strip():
        return "Error: url required."
    req = session
    if req is None:
        from suijin.modules.platform.lib.runtime import global_session

        req = global_session
    try:
        resp = req.get(str(url), timeout=20)
        body = resp.text
    except Exception as e:
        return f"Error fetching {url}: {e}"
    words = extract_words(body, min_len=min_len, max_len=max_len)
    if not words:
        return f"No words extracted from {url}."
    if not out:
        from urllib.parse import urlparse

        host = urlparse(str(url)).hostname or "target"
        out = f"wordlists/cewl_{host.replace('.', '_')}.txt"
    target = _resolve(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(words) + "\n")
    return f"harvested {len(words):,} word(s) from {url} -> {target.relative_to(_ws_dir())}"
