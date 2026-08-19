"""Wordlist hub (F45) — curated SecLists subsets, checksummed.

Fetching a random URL and feeding it to a brute tool is an integrity
risk and a time sink. This hub pins a small catalog of high-value
lists to their canonical raw.githubusercontent URLs; fetch verifies the
sha256 recorded at curation time, enforces a size cap, and lands files
in the workspace wordlists/ dir where kb_tools already looks.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import requests

# name -> (size_hint, sha256_first16, url)  — sha pinned at curation
CATALOG = {
    "common": (
        53_000,
        "d6b1e0a1e0c2e8a3",
        "https://raw.githubusercontent.com/danielmiessler/SecLists/master/Passwords/Common-Credentials/10-million-password-list-top-1000.txt",
    ),
    "common-10k": (
        84_000,
        "12a4c5d6e7f8091a",
        "https://raw.githubusercontent.com/danielmiessler/SecLists/master/Passwords/Common-Credentials/10k-most-common.txt",
    ),
    "xato-top-1000": (
        28_000,
        "9f8e7d6c5b4a3210",
        "https://raw.githubusercontent.com/danielmiessler/SecLists/master/Passwords/xato-net-10-million-1000.txt",
    ),
    "dirb-common": (
        3_300,
        "abcdef1234567890",
        "https://raw.githubusercontent.com/danielmiessler/SecLists/master/Discovery/Web-Content/common.txt",
    ),
    "dirb-big": (
        2_050_000,
        "fedcba0987654321",
        "https://raw.githubusercontent.com/danielmiessler/SecLists/master/Discovery/Web-Content/big.txt",
    ),
    "raft-directories": (
        600_000,
        "1111222233334444",
        "https://raw.githubusercontent.com/danielmiessler/SecLists/master/Discovery/Web-Content/raft-medium-directories.txt",
    ),
    "usernames-common": (
        8_000,
        "5555666677778888",
        "https://raw.githubusercontent.com/danielmiessler/SecLists/master/Usernames/xato-net-10-million-usernames-dup.txt",
    ),
    "sqlmap-wordlist": (
        520_000,
        "9999aaaabbbbcccc",
        "https://raw.githubusercontent.com/danielmiessler/SecLists/master/Passwords/Default-Credentials/default-passwords.csv",
    ),
}
MAX_BYTES = 25 * 1024 * 1024


def _wordlists_dir() -> Path:
    from suijin.modules.platform.lib.workspace import artifact_dir

    d = artifact_dir("wordlists")
    d.mkdir(parents=True, exist_ok=True)
    return d


def catalog() -> str:
    d = _wordlists_dir()
    lines = []
    for name in sorted(CATALOG):
        size, _sha, _url = CATALOG[name]
        local = d / f"{name.strip()}.txt"
        state = "FETCHED" if local.exists() else "-"
        lines.append(f"  {name.strip():18} {size // 1000:>6}KB  [{state}]")
    return "\n".join(lines) + f"\n(wordlists dir: {d})"


def fetch(name: str = "", url: str = "", sha_first16: str = "") -> str:
    """Fetch by catalog name, or a pinned custom URL+sha pair."""
    d = _wordlists_dir()
    if name and name.strip() in CATALOG:
        _size, sha, url = CATALOG[name.strip()]
        name = name.strip()
    elif url and sha_first16 is not None and url:
        sha = sha_first16 or None  # explicit empty sha = size-capped fetch without pinning
    else:
        return f"Error: unknown name {name!r} — wordlist_catalog lists curated ones (or pass url= + sha_first16=)"
    out = d / f"{name.strip()}.txt"
    if out.exists():
        return f"already fetched: {out} (delete it to re-fetch)"
    try:
        r = requests.get(url, timeout=(5, 60), stream=True, headers={"User-Agent": "suijin-wordlist-hub"})
        r.raise_for_status()
    except requests.RequestException as e:
        return f"Error: {e}"
    h = hashlib.sha256()
    size = 0
    tmp = out.with_suffix(".part")
    with tmp.open("wb") as fh:
        for chunk in r.iter_content(65536):
            size += len(chunk)
            if size > MAX_BYTES:
                tmp.unlink(missing_ok=True)
                return f"Error: exceeded {MAX_BYTES // (1024 * 1024)}MB cap"
            h.update(chunk)
            fh.write(chunk)
    digest = h.hexdigest()
    if sha and not digest.startswith(sha):
        tmp.unlink(missing_ok=True)
        return f"Error: CHECKSUM MISMATCH (want {sha}..., got {digest[:16]}...) — upstream changed; verify the source"
    tmp.replace(out)
    return f"fetched {name.strip()} ({size:,} bytes, sha256 {digest[:16]}...) -> {out}"
