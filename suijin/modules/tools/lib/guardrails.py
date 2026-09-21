"""Command safety guardrails — extracted from dispatch.py for maintainability."""

from __future__ import annotations

import os

_BLOCKED_PATTERNS = [
    "rm -rf /",
    "rm -rf ~",
    "rm -rf .",
    "mkfs.",
    "dd if=",
    ":(){ :|:& };:",
    "> /dev/sda",
    "chmod 777 /",
    "wget .* -O /tmp/.*\\|.*sh",
    "curl .*\\|.*sh",
    "sudo rm -rf",
    "sudo shutdown",
    "sudo reboot",
    "sudo halt",
    "> /etc/passwd",
    "> /etc/shadow",
]


def is_dangerous(cmd: str):
    """Check command against blocked patterns. Returns (is_dangerous, pattern).

    The patterns are REGEX-SHAPED (``curl .*\\|.*sh``) — the old plain
    substring check never matched them, so the intended blocks (curl|sh
    pipe-to-shell among them) were silently inert. Regex both sides,
    spaces stripped, exactly like the original intent."""
    import re

    cmd_lower = cmd.lower().replace(" ", "")
    for pattern in _BLOCKED_PATTERNS:
        p = pattern.lower().replace(" ", "")
        try:
            if re.search(p, cmd_lower):
                return True, pattern
        except re.error:
            if p in cmd_lower:  # a non-regex pattern still works as a substring
                return True, pattern
    return False, None


def confirm_global_action(cmd: str, pattern: str) -> bool:
    """Require operator confirmation for dangerous commands."""
    if os.environ.get("SUIJIN_AUTO_APPROVE", "").lower() == "true":
        return True
    try:
        from rich.console import Console as _RichConsole

        _RichConsole(stderr=True).print(f"  [bold red]BLOCKED:[/bold red] '{pattern}' matched in command")
    except Exception:
        pass
    return False
