"""Credential store — persist discovered credentials."""

import json
import time
from pathlib import Path

try:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR
except Exception:  # loaded outside the suijin process — same layout by convention
    WORKSPACE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "suijin_agent"

STORE_PATH = WORKSPACE_DIR / "credentials.json"


def _load_store():
    if not STORE_PATH.exists():
        return {"_schema": "suijin-credentials-v1", "credentials": []}
    return json.loads(STORE_PATH.read_text())


def _save_store(data):
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(data, indent=2))


def env_name_for(service: str) -> str:
    """The env-var name a stored credential is exported under."""
    import re as _re

    return "SUIJIN_CRED_" + _re.sub(r"[^A-Z0-9]+", "_", str(service).upper()).strip("_")


def cred_env() -> dict:
    """SUIJIN_CRED_<NAME> -> value for every stored credential.

    execute_terminal exports these into each command's environment so the
    agent writes "$SUIJIN_CRED_<NAME>" in commands — the literal secret
    never lands in command text, transcripts, or artifacts.
    """
    out = {}
    for c in _load_store().get("credentials", []):
        val = str(c.get("value", "") or "").strip()
        if c.get("service") and val:
            out[env_name_for(c["service"])] = val
    return out


def creds_add(service="", cred_type="", value="", username="", notes="", **aliases):
    """Tolerant to argument drift from the model: note→notes, type→cred_type,
    token/key/password shorthand → value. The old rigid signature returned
    'unexpected keyword argument' and the credential was LOST."""
    notes = notes or aliases.get("note") or ""
    cred_type = cred_type or aliases.get("type") or aliases.get("kind") or ""
    value = value or aliases.get("token") or aliases.get("key") or aliases.get("password") or ""
    username = username or aliases.get("user") or ""
    if not service or not value:
        return "Error: service and value required (service=<target/system>, value=<the secret>, type=<password/token/key/cookie>, note=<context>)"
    store = _load_store()
    entry = {
        "service": service,
        "type": cred_type,
        "value": value,
        "username": username or "",
        "notes": notes or "",
        "discovered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _use = (
        f"Use it as ${env_name_for(service)} — exported to every execute_terminal "
        f"command automatically. NEVER paste the literal value into commands, files, or reports."
    )
    # Deduplicate
    for c in store["credentials"]:
        if c.get("service") == service and c.get("value") == value:
            c.update(entry)
            _save_store(store)
            return f"Updated existing credential: {service}\n{_use}"
    store["credentials"].append(entry)
    _save_store(store)
    return f"Credential stored: {service} ({cred_type})\n{_use}"


def creds_list(filter=""):
    store = _load_store()
    creds = store.get("credentials", [])
    if filter:
        f = filter.lower()
        creds = [c for c in creds if f in c.get("service", "").lower() or f in c.get("type", "").lower()]
    if not creds:
        return "(no credentials stored)"
    lines = []
    for c in creds:
        # MASKED (2026-10-27): the old listing printed the first 60 chars
        # of every secret into tool output — transcripts carried the
        # literal creds. Show a prefix + length and the env var instead.
        val = str(c.get("value", "") or "")
        masked = f"{val[:3]}***(len {len(val)})" if val else "(empty)"
        lines.append(
            f"[{c.get('type', '?')}] {c.get('service', '?')}: {masked} — use ${env_name_for(c.get('service', '?'))} (user: {c.get('username', '?')})"
        )
    return "\n".join(lines)


def creds_get(service):
    if not service:
        return "Error: service required"
    store = _load_store()
    for c in store.get("credentials", []):
        if c.get("service") == service:
            return json.dumps(c, indent=2)
    return f"No credentials found for '{service}'"
