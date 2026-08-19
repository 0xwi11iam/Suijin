import hashlib
from pathlib import Path


def crack_hash(hash_str: str = "", wordlist: str = "", algo: str = "") -> str:
    h = (hash_str or "").strip().lower()
    if not h or not wordlist:
        return "Error: hash and wordlist required"
    wl = Path(wordlist).expanduser()
    if not wl.is_file():
        return f"Error: wordlist not found: {wl}"
    if not algo:
        algo = {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}.get(len(h), "")
        if not algo:
            return f"Error: cannot infer algo from length {len(h)}; pass algo= explicitly"
    algo = algo.lower().replace("-", "")
    try:
        fn = getattr(hashlib, algo)
    except AttributeError:
        return f"Error: unsupported algo {algo!r}"
    checked = 0
    try:
        with open(wl, "rb") as f:
            for line in f:
                word = line.rstrip(b"\r\n")
                checked += 1
                if fn(word).hexdigest() == h:
                    return f"CRACKED [{algo}] after {checked:,} words: {word.decode('utf-8', 'replace')}"
                if fn(word.decode("utf-8", "replace")).hexdigest() == h:
                    return f"CRACKED [{algo}] after {checked:,} words: {word.decode('utf-8', 'replace')}"
    except OSError as e:
        return f"Error reading wordlist: {e}"
    return f"Not found: {checked:,} words tried against {algo} {h[:16]}..."
