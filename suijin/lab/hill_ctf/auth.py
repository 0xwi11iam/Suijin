"""Hill CTF JWT machinery — the two-step auth flaw.

Stage 2 (foothold): the token is HS256-signed with a weak secret
("hilltop" — guessable/brutable), AND the decoder accepts alg:none
(unsigned) tokens when the header claims it. Either path forges admin.
Tamper attempts emit jwt_tamper events (blue's signal).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from events import emit

JWT_SECRET = "hilltop"  # weak on purpose — the two-step flaw's step 1
TOKEN_TTL = 3600


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def issue(username: str, role: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": username, "role": role, "iat": int(time.time()), "exp": int(time.time()) + TOKEN_TTL}
    signing_input = _b64(json.dumps(header).encode()) + "." + _b64(json.dumps(payload).encode())
    sig = hmac.new(JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64(sig)


def decode(token: str):
    """Returns (claims, None) or (None, reason). The flaw: alg:none is
    ACCEPTED (any unsigned token with a payload validates)."""
    try:
        h_b64, p_b64, sig = token.split(".")
        header = json.loads(_unb64(h_b64))
        claims = json.loads(_unb64(p_b64))
    except (ValueError, json.JSONError):
        emit("jwt_tamper", detail="malformed token", token=token[:40])
        return None, "malformed token"

    if header.get("alg") == "none":
        # FLAW: signature verification skipped entirely
        if claims.get("exp") and claims["exp"] < time.time():
            return None, "expired"
        return claims, None  # accepted!

    if header.get("alg") != "HS256":
        emit("jwt_tamper", detail=f"unexpected alg {header.get('alg')}", sub=claims.get("sub"))
        return None, "unsupported alg"

    signing_input = f"{h_b64}.{p_b64}"
    expected = hmac.new(JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _unb64(sig)):
        emit("jwt_tamper", detail="bad signature", sub=claims.get("sub"))
        return None, "bad signature"
    if claims.get("exp") and claims["exp"] < time.time():
        return None, "expired"
    return claims, None
