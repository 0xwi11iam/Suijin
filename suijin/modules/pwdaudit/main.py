import re


def check_password_strength(password: str = "") -> str:
    p = password or ""
    if not p:
        return "Error: password required"
    checks = {
        "length>=12": len(p) >= 12,
        "length>=16": len(p) >= 16,
        "lower": bool(re.search(r"[a-z]", p)),
        "upper": bool(re.search(r"[A-Z]", p)),
        "digit": bool(re.search(r"\d", p)),
        "symbol": bool(re.search(r"[^a-zA-Z0-9]", p)),
    }
    common = p.lower() in (
        "password",
        "admin",
        "welcome",
        "letmein",
        "changeme",
        "qwerty",
        "123456",
        "password1",
        "p@ssw0rd",
        "admin123",
    )
    seq = bool(re.search(r"(0123|1234|2345|3456|4567|5678|6789|abcd|bcde|qwer|asdf|zxcv)", p.lower()))
    score = sum(checks.values())
    verdict = "WEAK" if score <= 3 else "MODERATE" if score <= 5 else "STRONG"
    flags = [f"+ {k}" if v else f"- {k}" for k, v in checks.items()]
    if common:
        verdict = "TRIVIAL (top-10 common password)"
        flags.append("! dictionary-top-10")
    if seq:
        flags.append("! keyboard/number sequence")
    return f"{verdict} (score {score}/7)\n" + "\n".join(flags)
