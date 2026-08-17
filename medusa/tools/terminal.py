"""Shell execution gateway for Medusa tools.

Scoped to the agent workspace with destructive-command guardrails.
"""

from __future__ import annotations

import os
import shlex

from medusa.tools.guardrails import confirm_global_action, is_dangerous
from medusa.tools.workspace import WORKSPACE_DIR

from .result import run_command
from .runtime import truncate


def execute_terminal(cmd, timeout=30):
    """Shell execution gateway — scoped to medusa_agent/ workspace.

    Commands that modify the global system (pip install, brew, apt, sudo, etc.)
    are intercepted and require explicit user approval before execution.
    """
    try:
        if not cmd:
            return "Error: No command provided."

        # Self-Kill Protection
        my_pid = str(os.getpid())
        cmd_tokens = cmd.replace(";", " ").replace("&&", " ").replace("|", " ").split()
        if "kill" in cmd_tokens and my_pid in cmd_tokens:
            return f"SYSTEM OVERRIDE: Refusing to execute command. {my_pid} is the AI Agent's own Process ID. You must find the target application's PID."

        # Global-action gate: intercept dangerous commands
        dangerous, pattern = is_dangerous(cmd)
        if dangerous and not confirm_global_action(cmd, pattern):
            return f"Command denied by user (matched: {pattern}).\nCommand was: {cmd[:200]}"
        # Approved (or not dangerous) — proceed with execution

        # Build environment with homebrew paths (macOS)
        env = os.environ.copy()
        brew_paths = ["/opt/homebrew/bin", "/usr/local/bin", "/opt/homebrew/sbin"]
        current_path = env.get("PATH", "")
        for bp in brew_paths:
            if bp not in current_path:
                current_path = f"{bp}:{current_path}"
        env["PATH"] = current_path

        # Tokenize; fall back to a shell one-liner for quoted/compound commands
        try:
            cmd_parts = shlex.split(cmd)
        except ValueError:
            cmd_parts = ["/bin/sh", "-c", cmd]

        result = run_command(
            cmd_parts if len(cmd_parts) > 1 else ["/bin/sh", "-c", cmd],
            timeout=timeout,
            cwd=str(WORKSPACE_DIR),
            env=env,
            command_text=cmd,
        )
        return truncate(result.format())
    except Exception as e:
        return f"Execution Fault: {str(e)}"
