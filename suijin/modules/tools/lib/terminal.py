"""Shell execution gateway for Suijin tools.

Scoped to the agent workspace with destructive-command guardrails.
"""

from __future__ import annotations

import os
import shlex
import sys


def _ws():
    """Platform workspace accessors, resolved lazily (module boundary rule)."""
    from suijin.modules.platform.lib import workspace

    return workspace


from .result import run_command


def execute_terminal(cmd, timeout=30):
    """Shell execution gateway — scoped to suijin_agent/ workspace.

    Commands that modify the global system (pip install, brew, apt, sudo, etc.)
    are intercepted and require explicit user approval before execution.
    """
    try:
        if not cmd:
            return "Error: No command provided."

        # Suijin does not run as native Python on Windows — the tool
        # ecosystem (packs, POSIX shell) is Linux-based. Windows users
        # run the container via install.ps1 / docker compose.
        if sys.platform == "win32":
            return (
                "Error: native Windows is not supported. Suijin runs on macOS, "
                "Linux, or via Docker on Windows — use install.ps1 to set up the "
                "container, then run commands inside it."
            )

        # Self-Kill Protection
        my_pid = str(os.getpid())
        cmd_tokens = cmd.replace(";", " ").replace("&&", " ").replace("|", " ").split()
        if "kill" in cmd_tokens and my_pid in cmd_tokens:
            return f"SYSTEM OVERRIDE: Refusing to execute command. {my_pid} is the AI Agent's own Process ID. You must find the target application's PID."

        # Global-action gate: intercept dangerous commands
        from suijin.modules.tools.lib.guardrails import is_dangerous

        dangerous, pattern = is_dangerous(cmd)
        from suijin.modules.tools.lib.guardrails import confirm_global_action

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

        # CRITICAL (v5.2): commands containing shell metacharacters
        # (;|&><$`() must run through a shell — shlex.split succeeds on
        # them but the tokens are meaningless as argv, so "a; b" was
        # exec'd as a literal ";" argument. This broke every pipeline
        # and chained command in three documented field engagements.
        import re as _re

        needs_shell = bool(_re.search(r"[;|&><$`()]", cmd))
        if needs_shell:
            cmd_parts = ["/bin/sh", "-c", cmd]
        else:
            # Tokenize; fall back to a shell one-liner for quoted/compound commands
            try:
                cmd_parts = shlex.split(cmd)
            except ValueError:
                cmd_parts = ["/bin/sh", "-c", cmd]

        # Stealth (v5.1): loud tools get tool-level rate caps — same work,
        # same parallelism, just not machine-gun fast. Benign commands
        # and operator-throttled invocations pass through untouched.
        try:
            from suijin.modules.platform.lib.stealth import sanitize_command

            if len(cmd_parts) > 1 and cmd_parts[0] != "/bin/sh":
                cmd_parts = sanitize_command(cmd_parts)
        except Exception:  # noqa: BLE001 — never block execution
            pass
        result = run_command(
            cmd_parts if len(cmd_parts) > 1 else ["/bin/sh", "-c", cmd],
            timeout=timeout,
            cwd=str(_ws().WORKSPACE_DIR),
            env=env,
            command_text=cmd,
        )
        from suijin.modules.platform.lib.runtime import truncate

        return truncate(result.format())
    except Exception as e:
        return f"Execution Fault: {str(e)}"
