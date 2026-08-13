"""Action dispatcher — execute defense actions."""
from __future__ import annotations
def dispatch(action: str, target: str, metadata: dict = None) -> str:
    actions = {"block_ip": lambda: f"BLOCKED {target}", "rate_limit": lambda: f"RATE LIMITED {target}",
               "honeypot": lambda: f"HONEYPOT DEPLOYED for {target}",
               "shadow_redirect": lambda: f"SHADOW REDIRECTED {target}",
               "notify_operator": lambda: f"OPERATOR NOTIFIED: {target}"}
    return actions.get(action, lambda: f"Unknown action: {action}")()
