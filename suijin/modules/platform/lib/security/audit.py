"""
suijin/audit.py
=================
Enterprise audit trail system for Suijin.
Logs every agent action with timestamp, user, target, and result.
Includes admin bypass key for testing.
"""

import hashlib
import json
import os
import secrets
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[4]
AUDIT_LOG = BASE_DIR / "audit_log.json"
KEYS_FILE = BASE_DIR / "access_keys.json"

# Admin key — loaded from environment, never hardcoded
ADMIN_KEY = os.environ.get("SUIJIN_ADMIN_KEY", hashlib.sha256(secrets.token_bytes(32)).hexdigest()[:16])

# Default enterprise keys
DEFAULT_KEYS = {
    ADMIN_KEY: {"role": "admin", "name": "Admin", "allowed_targets": "*", "created": datetime.now().isoformat()}
}


def _load_keys():
    if KEYS_FILE.exists():
        with open(KEYS_FILE, "r") as f:
            return json.load(f)
    else:
        _save_keys(DEFAULT_KEYS)
        return DEFAULT_KEYS


def _save_keys(keys):
    with open(KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)


def _load_log():
    if AUDIT_LOG.exists():
        with open(AUDIT_LOG, "r") as f:
            return json.load(f)
    return []


def _save_log(log):
    with open(AUDIT_LOG, "w") as f:
        json.dump(log, f, indent=2)


def verify_access(api_key: str, target: str) -> dict:
    """
    Verify if the user has access to test the given target.
    Returns dict with 'allowed' bool and 'reason' string.
    """
    keys = _load_keys()

    if api_key not in keys:
        return {"allowed": False, "reason": "Invalid API key. Contact admin for access.", "role": None}

    key_data = keys[api_key]
    allowed_targets = key_data.get("allowed_targets", "")

    # Admin can test any target
    if key_data["role"] == "admin" or allowed_targets == "*":
        return {"allowed": True, "reason": "Admin access granted.", "role": "admin", "user": key_data["name"]}

    # Check if target is in allowed list
    if isinstance(allowed_targets, list):
        for allowed in allowed_targets:
            if allowed in target:
                return {
                    "allowed": True,
                    "reason": f"Target authorized for {key_data['name']}.",
                    "role": key_data["role"],
                    "user": key_data["name"],
                }

    return {
        "allowed": False,
        "reason": f"Target '{target}' not authorized for your key. Add target ownership proof first.",
        "role": key_data["role"],
    }


def log_action(
    api_key: str,
    action: str,
    target: str,
    result: str,
    mode: str = "red",
    risk_score: float = 0.0,
    details: dict = None,
):
    """
    Log an agent action to the audit trail.
    """
    keys = _load_keys()
    user = keys.get(api_key, {}).get("name", "unknown")
    role = keys.get(api_key, {}).get("role", "unknown")

    entry = {
        "timestamp": datetime.now().isoformat(),
        "user": user,
        "role": role,
        "mode": mode,
        "target": target,
        "action": action,
        "result": result,
        "risk_score": risk_score,
        "details": details or {},
    }

    log = _load_log()
    log.append(entry)
    _save_log(log)
    return entry


def generate_key(name: str, allowed_targets: list = None, role: str = "user") -> str:
    """
    Generate a new API key for a user.
    """
    key = "SUIJIN-" + secrets.token_hex(8).upper()
    keys = _load_keys()
    keys[key] = {
        "role": role,
        "name": name,
        "allowed_targets": allowed_targets or [],
        "created": datetime.now().isoformat(),
    }
    _save_keys(keys)
    return key


def get_audit_report(api_key: str = None, limit: int = 50) -> list:
    """
    Get recent audit log entries.
    Admin can see all, users see only their own.
    """
    keys = _load_keys()
    log = _load_log()

    if api_key and keys.get(api_key, {}).get("role") != "admin":
        user = keys.get(api_key, {}).get("name")
        log = [e for e in log if e.get("user") == user]

    return log[-limit:]


def print_audit_report(api_key: str = None):
    """
    Print a formatted audit report to console.
    """
    from rich.console import Console
    from rich.table import Table

    console = Console()
    entries = get_audit_report(api_key)

    if not entries:
        console.print("[yellow]No audit entries found.[/yellow]")
        return

    table = Table(title="Suijin Audit Trail")
    table.add_column("Timestamp", style="cyan")
    table.add_column("User", style="green")
    table.add_column("Mode", style="magenta")
    table.add_column("Target", style="yellow")
    table.add_column("Action", style="white")
    table.add_column("Result", style="red")
    table.add_column("Risk", style="red")

    for e in entries:
        table.add_row(
            e["timestamp"][:19],
            e["user"],
            e["mode"],
            e["target"][:30],
            e["action"][:40],
            e["result"][:20],
            str(e["risk_score"]),
        )

    console.print(table)


if __name__ == "__main__":
    # Quick test
    print("Testing audit system...")

    # Test admin access
    result = verify_access(ADMIN_KEY, "http://127.0.0.1:5000")
    print(f"Admin access: {result}")

    # Log a test action
    log_action(
        api_key=ADMIN_KEY,
        action="SQL Injection attempt on /login",
        target="http://127.0.0.1:5000",
        result="SUCCESS",
        mode="red",
        risk_score=0.85,
    )

    # Generate a test user key
    new_key = generate_key("TestUser", ["http://testsite.com"])
    print(f"Generated key: {new_key}")

    # Test user access
    result = verify_access(new_key, "http://testsite.com")
    print(f"User access (authorized target): {result}")

    result = verify_access(new_key, "http://unauthorized.com")
    print(f"User access (unauthorized target): {result}")

    # Print report
    print_audit_report(ADMIN_KEY)
