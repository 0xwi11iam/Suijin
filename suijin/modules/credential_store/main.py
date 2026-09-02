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
    # Deduplicate
    for c in store["credentials"]:
        if c.get("service") == service and c.get("value") == value:
            c.update(entry)
            _save_store(store)
            return f"Updated existing credential: {service}"
    store["credentials"].append(entry)
    _save_store(store)
    return f"Credential stored: {service} ({cred_type})"


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
        lines.append(
            f"[{c['type']}] {c['service']}: {c['value'][:60]}{'...' if len(c['value']) > 60 else ''} (user: {c.get('username', '?')})"
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
