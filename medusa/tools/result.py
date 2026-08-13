"""Structured command results.

Every tool that shells out should return a `CommandResult` so the oracle can
reason over machine-readable fields (command, exit code, stdout, stderr,
duration) instead of parsing free text.
"""
from __future__ import annotations

import subprocess
import time


class CommandResult:
    __slots__ = ("command", "exit_code", "stdout", "stderr", "duration_ms")

    def __init__(self, command: str, exit_code: int | None, stdout: str, stderr: str, duration_ms: int):
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = duration_ms

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
        }

    def format(self) -> str:
        seconds = f"{self.duration_ms / 1000:.1f}s" if self.duration_ms is not None else "?"
        code = str(self.exit_code) if self.exit_code is not None else "?"
        out = f"[COMMAND] {self.command}\n[EXIT] {code} ({seconds})\n"
        if self.stdout:
            out += f"[STDOUT]\n{self.stdout}\n"
        if self.stderr:
            out += f"[STDERR]\n{self.stderr}\n"
        if not self.stdout and not self.stderr:
            out += "[STDOUT]\n(no output)\n"
        return out


def run_command(cmd, *, timeout=300, cwd=None, env=None, shell=False, command_text=None) -> CommandResult:
    """Run a command and wrap the result.

    `cmd` may be a string (with shell=True) or a list of arguments (shell=False).
    Exceptions (timeout, missing binary) are captured as a CommandResult with
    exit_code -1 so callers can format them uniformly.
    """
    display = command_text or (cmd if isinstance(cmd, str) else " ".join(str(p) for p in cmd))
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
            shell=shell,
        )
        return CommandResult(
            display,
            proc.returncode,
            proc.stdout or "",
            proc.stderr or "",
            int((time.time() - start) * 1000),
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            display,
            -1,
            "",
            f"timed out after {timeout}s",
            int((time.time() - start) * 1000),
        )
    except FileNotFoundError as e:
        return CommandResult(
            display,
            -1,
            "",
            f"command not found: {e}",
            int((time.time() - start) * 1000),
        )
    except Exception as e:
        return CommandResult(
            display,
            -1,
            "",
            f"execution fault: {e}",
            int((time.time() - start) * 1000),
        )
