import secrets
import string
import uuid as _uuid


def random_token(length: int = 32, charset: str = "hex") -> str:
    n = max(4, min(int(length or 32), 256))
    cs = (charset or "hex").lower()
    if cs == "hex":
        return secrets.token_hex(n // 2 + 1)[:n]
    if cs == "alnum":
        alphabet = string.ascii_letters + string.digits
    else:
        alphabet = string.ascii_letters + string.digits + string.punctuation
    return "".join(secrets.choice(alphabet) for _ in range(n))


def password_gen(count: int = 3, length: int = 20) -> str:
    n = max(1, min(int(count or 3), 20))
    ln = max(12, min(int(length or 20), 128))
    out = []
    for _ in range(n):
        while True:
            pw = "".join(secrets.choice(string.ascii_letters + string.digits + "!@#$%^&*") for _ in range(ln))
            if (
                any(c.islower() for c in pw)
                and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw)
                and any(c in "!@#$%^&*" for c in pw)
            ):
                break
        out.append(pw)
    return "\n".join(out)


def uuid_gen(count: int = 1) -> str:
    n = max(1, min(int(count or 1), 50))
    return "\n".join(str(_uuid.uuid4()) for _ in range(n))
