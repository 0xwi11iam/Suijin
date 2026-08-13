"""Shell execution gateway for Medusa tools.

Scoped to the agent workspace with destructive-command guardrails.
"""
from __future__ import annotations

import os
import shlex
import subprocess

from medusa.tools.guardrails import is_dangerous, confirm_global_action
from medusa.tools.workspace import WORKSPACE_DIR

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
        if dangerous:
            if not confirm_global_action(cmd, pattern):
                return f"Command denied by user (matched: {pattern}).\nCommand was: {cmd[:200]}"
            # Approved — proceed with execution

        # Build environment with homebrew paths (macOS)
        env = os.environ.copy()
        brew_paths = ['/opt/homebrew/bin', '/usr/local/bin', '/opt/homebrew/sbin']
        current_path = env.get('PATH', '')
        for bp in brew_paths:
            if bp not in current_path:
                current_path = f"{bp}:{current_path}"
        env['PATH'] = current_path

        # Run with shell=False using tokenized command list
        try:
            cmd_parts = shlex.split(cmd)
        except ValueError:
            cmd_parts = ["/bin/sh", "-c", cmd]

        process = subprocess.run(
            cmd_parts if len(cmd_parts) > 1 else ["/bin/sh", "-c", cmd],
            capture_output=True, text=True,
            timeout=timeout, cwd=str(WORKSPACE_DIR), env=env,
        )
        out = ""
        if process.stdout:
            out += f"[STDOUT]\n{process.stdout}\n"
        if process.stderr:
            out += f"[STDERR]\n{process.stderr}\n"
        return truncate(out if out else "Executed (No Output).")
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds (no output was received)."
    except Exception as e:
        return f"Execution Fault: {str(e)}"
