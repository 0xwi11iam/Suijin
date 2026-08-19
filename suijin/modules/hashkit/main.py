import hashlib
import hmac
from pathlib import Path


def hash_compute(text: str = "", file: str = "", algo: str = "sha256") -> str:
    a = (algo or "sha256").lower().replace("-", "")
    try:
        fn = getattr(hashlib, a)
    except AttributeError:
        return f"Error: unknown algo {algo!r}"
    if file:
        p = Path(file).expanduser()
        if not p.is_file():
            return f"Error: {p} not found"
        h = fn()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return f"{a}({p.name}) = {h.hexdigest()}"
    if not text:
        return "Error: text or file required"
    return f"{a} = {fn(text.encode()).hexdigest()}"


def hmac_sign(text: str = "", key: str = "", algo: str = "sha256") -> str:
    if not text or not key:
        return "Error: text and key required"
    a = (algo or "sha256").lower()
    try:
        mac = hmac.new(key.encode(), text.encode(), a)
    except Exception:
        return f"Error: algo {algo!r} unavailable for hmac"
    return f"hmac-{a} = {mac.hexdigest()}"
