_KNOWN = {
    32: ["MD5", "NTLM", "MD4", "LM"],
    40: ["SHA1", "MySQL5", "RIPEMD160"],
    56: ["SHA224", "SHA3-224"],
    64: ["SHA256", "SHA3-256", "BLAKE2s"],
    96: ["SHA384", "SHA3-384"],
    128: ["SHA512", "SHA3-512", "BLAKE2b", "Whirlpool"],
    16: ["MySQL (old), CRC64"],
}


def identify_hash(hash_str: str = "") -> str:
    h = (hash_str or "").strip()
    if not h:
        return "Error: hash required"
    if h.startswith("$2"):
        return "bcrypt"
    if h.startswith("$6$"):
        return "sha512crypt (Unix)"
    if h.startswith("$1$"):
        return "md5crypt (Unix)"
    if h.startswith("$argon2"):
        return "Argon2"
    if h.count("$") == 2 and len(h.split("$")[-1]) == 27:
        return "Apache MD5 (apr1)"
    is_hex = all(c in "0123456789abcdefABCDEF" for c in h)
    cands = _KNOWN.get(len(h), [])
    notes = []
    if is_hex:
        notes.append(f"hex charset, {len(h)} chars")
        notes.append("candidates: " + ", ".join(cands) if cands else f"no common hex hash is {len(h)} chars")
    elif len(h) == 13 and h.isalnum():
        notes.append("DES crypt (Unix)")
    elif h.isalnum() and len(h) in (24,):
        notes.append("base64-ish — possibly SHA1 truncated or a token, not a plain hash")
    else:
        notes.append("mixed charset — custom/salted format or not a hash")
    return f"{h[:12]}... -> " + "; ".join(notes) if len(h) > 12 else f"{h} -> " + "; ".join(notes)
