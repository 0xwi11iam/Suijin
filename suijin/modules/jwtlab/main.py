import base64
import json
import time


def _b64url_decode(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def jwt_inspect(token: str = "") -> str:
    t = (token or "").strip()
    if not t:
        return "Error: token required"
    parts = t.split(".")
    if len(parts) != 3:
        return f"Error: expected 3 dot-separated segments, got {len(parts)}"
    try:
        header = json.loads(_b64url_decode(parts[0]))
        payload = json.loads(_b64url_decode(parts[1]))
    except Exception as e:
        return f"Error: undecodable segment: {e}"
    out = [f"header: {json.dumps(header)}", f"payload: {json.dumps(payload, indent=2)}"]
    alg = str(header.get("alg", "")).lower()
    notes = []
    if alg in ("none", ""):
        notes.append("CRITICAL: alg=none — token can be forged by dropping the signature")
    if alg.startswith("hs"):
        notes.append(
            "HMAC: check for weak secrets (wordlist attack) and alg-confusion (server must reject rs* on hs tokens)"
        )
    if alg.startswith("rs") or alg.startswith("es"):
        notes.append("asymmetric — verify the key; check kid injection (SQL/LDAP in kid param)")
    if header.get("jku") or header.get("x5u"):
        notes.append("key-url header present (jku/x5u) — SSRF/key-substitution risk if server fetches it")
    exp = payload.get("exp")
    if isinstance(exp, (int, float)):
        left = exp - time.time()
        notes.append(
            f"exp: {'EXPIRED' if left < 0 else f'valid for {int(left // 60)}m'} ({time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime(exp))})"
        )
    else:
        notes.append("no exp claim — token may never expire")
    if not payload.get("aud"):
        notes.append("no aud claim — check audience enforcement")
    out.append("audit: " + ("; ".join(notes) if notes else "no obvious issues"))
    out.append("claims: " + ", ".join(sorted(payload)))
    return "\n".join(out)
