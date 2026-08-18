"""Credential vault — discovered credentials encrypted at rest.

suijin_agent/credentials.vault.json holds PBKDF2-HMAC-SHA256 derived
keystream-XOR ciphertext with an HMAC-SHA256 integrity tag. This is
obfuscation-grade authenticated encryption from the stdlib (no third-party
crypto dependency) — it protects artifacts copied off the engagement box,
not a determined attacker with the passphrase.

CLI: suijin creds init|list|add|get|export|--redact. The plaintext
credentials.json (legacy credential_store format) is imported on init and
shredded after successful encryption.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from suijin.tools.workspace import WORKSPACE_DIR

VAULT_PATH = WORKSPACE_DIR / "credentials.vault.json"
LEGACY_PATH = WORKSPACE_DIR / "credentials.json"
PBKDF2_ITERS = 200_000
_SALT_BYTES = 16


def _derive(passphrase: str, salt: bytes) -> tuple[bytes, bytes]:
    """Return (enc_key, mac_key) via PBKDF2-HMAC-SHA256."""
    master = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, PBKDF2_ITERS)
    return (
        hashlib.sha256(b"enc" + master).digest(),
        hashlib.sha256(b"mac" + master).digest(),
    )


def _keystream_xor(key: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    block = 0
    while block * 32 < len(data):
        ks = hmac.new(key, str(block).encode(), hashlib.sha256).digest()
        chunk = data[block * 32 : (block + 1) * 32]
        out[block * 32 : (block + 1) * 32] = bytes(a ^ b for a, b in zip(chunk, ks, strict=False))
        block += 1
    return bytes(out)


def vault_exists() -> bool:
    return VAULT_PATH.exists()


def save_vault(entries: list[dict], passphrase: str) -> None:
    """Encrypt and atomically write the entry list."""
    salt = secrets.token_bytes(_SALT_BYTES)
    enc_key, mac_key = _derive(passphrase, salt)
    plaintext = json.dumps({"credentials": entries}).encode()
    ciphertext = _keystream_xor(enc_key, plaintext)
    tag = hmac.new(mac_key, ciphertext, hashlib.sha256).hexdigest()
    blob = {
        "v": 1,
        "kdf": f"pbkdf2-hmac-sha256-{PBKDF2_ITERS}",
        "salt": base64.b64encode(salt).decode(),
        "tag": tag,
        "ct": base64.b64encode(ciphertext).decode(),
    }
    tmp = VAULT_PATH.with_suffix(".part")
    tmp.write_text(json.dumps(blob))
    os.chmod(tmp, 0o600)
    tmp.replace(VAULT_PATH)


def load_vault(passphrase: str) -> list[dict]:
    """Decrypt and verify; raises PermissionError on bad passphrase/tag."""
    blob = json.loads(VAULT_PATH.read_text())
    salt = base64.b64decode(blob["salt"])
    ciphertext = base64.b64decode(blob["ct"])
    enc_key, mac_key = _derive(passphrase, salt)
    expect = hmac.new(mac_key, ciphertext, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, blob["tag"]):
        raise PermissionError("vault: wrong passphrase or corrupted vault")
    return json.loads(_keystream_xor(enc_key, ciphertext)).get("credentials", [])


def init_vault(passphrase: str, import_legacy: bool = True) -> str:
    """Create the vault, importing + shredding legacy credentials.json."""
    entries: list[dict] = []
    if import_legacy and LEGACY_PATH.exists():
        try:
            data = json.loads(LEGACY_PATH.read_text())
            entries = [e for e in data.get("credentials", []) if isinstance(e, dict)]
        except ValueError:
            entries = []
    save_vault(entries, passphrase)
    if import_legacy and LEGACY_PATH.exists() and entries:
        LEGACY_PATH.unlink()  # plaintext copy must not survive
    return f"vault initialized with {len(entries)} credential(s) at {VAULT_PATH.name}"


def add_credential(
    service: str, cred_type: str, value: str, username: str = "", notes: str = "", passphrase: str = ""
) -> str:
    if not vault_exists():
        return "No vault — run: suijin creds init"
    entries = load_vault(passphrase)
    if any(c.get("service") == service and c.get("value") == value for c in entries):
        return "Already in vault (deduplicated)."
    entries.append(
        {
            "service": service,
            "type": cred_type,
            "value": value,
            "username": username,
            "notes": notes,
            "discovered_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    save_vault(entries, passphrase)
    return f"Stored: {service} ({cred_type}). Vault now holds {len(entries)} credential(s)."


def list_credentials(passphrase: str, reveal: bool = False) -> str:
    if not vault_exists():
        return "No vault — run: suijin creds init"
    entries = load_vault(passphrase)
    if not entries:
        return "Vault is empty."
    lines = [f"{len(entries)} credential(s):"]
    for c in entries:
        val = c.get("value", "") if reveal else "••••••"
        lines.append(f"  {c.get('service', '?'):20} {c.get('type', '?'):12} {c.get('username', '') or '-':16} {val}")
    if not reveal:
        lines.append("\n(values hidden — `suijin creds list --reveal`)")
    return "\n".join(lines)


def export_credentials(passphrase: str, out_path: Path | None = None, redact: bool = True) -> str:
    """Write vault contents to a file; values redacted unless redact=False."""
    entries = load_vault(passphrase)
    if redact:
        entries = [{**c, "value": "***redacted***"} for c in entries]
    out = Path(out_path) if out_path else WORKSPACE_DIR / "reports" / "vault_export.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"credentials": entries}, indent=2))
    return (
        f"exported {len(entries)} credential(s) ({'REDACTED' if redact else 'PLAINTEXT — handle with care'}) -> {out}"
    )
