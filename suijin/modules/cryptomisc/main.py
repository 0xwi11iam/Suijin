def rot_n(text: str = "", shift: int = 0) -> str:
    if not text:
        return "Error: text required"

    def rot(s: str, n: int) -> str:
        out = []
        for c in s:
            if c.isalpha():
                base = 65 if c.isupper() else 97
                out.append(chr((ord(c) - base + n) % 26 + base))
            else:
                out.append(c)
        return "".join(out)

    try:
        n = int(shift) if shift else None
    except ValueError:
        n = None
    if n is not None and 0 < n < 26:
        return f"ROT{n}: {rot(text, n)}"
    return (
        "\n".join(f"ROT{n}: {rot(text, n)}" for n in (13, 7, 19, 3, 11, 5, 17, 21, 23))
        + "\n(pass shift= to see all 25)"
    )


def xor_decode(data_hex: str = "", key: str = "") -> str:
    if not data_hex or not key:
        return "Error: data_hex and key required"
    try:
        data = bytes.fromhex(data_hex.strip().replace(" ", ""))
    except ValueError:
        return "Error: data_hex is not valid hex"
    kb = key.encode()
    out = bytes(b ^ kb[i % len(kb)] for i, b in enumerate(data))
    printable = sum(32 <= b < 127 or b in (9, 10, 13) for b in out) / max(len(out), 1)
    return f"xor result ({int(printable * 100)}% printable):\nraw: {out!r}\nutf8: {out.decode('utf-8', 'replace')}"
