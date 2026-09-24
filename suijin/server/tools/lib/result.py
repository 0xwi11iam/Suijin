"""Structured command results.

Every tool that shells out should return a `CommandResult` so the oracle can
reason over machine-readable fields (command, exit code, stdout, stderr,
duration) instead of parsing free text.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
import time

# Thread-local stream sink: background job runners set this so that `run_command`
# streams each output line to the job as it arrives, instead of buffering it
# until the process exits.
_stream_sink = threading.local()


def set_stream_sink(fn) -> None:
    _stream_sink.fn = fn


def clear_stream_sink() -> None:
    if hasattr(_stream_sink, "fn"):
        del _stream_sink.fn


def _active_sink():
    return getattr(_stream_sink, "fn", None)


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

    When a stream sink is active on this thread, output lines are pushed to it
    live (used by the background job system for progress).
    """
    display = command_text or (cmd if isinstance(cmd, str) else " ".join(str(p) for p in cmd))
    sink = _active_sink()
    if sink is not None:
        return _run_streaming(cmd, timeout=timeout, cwd=cwd, env=env, shell=shell, display=display, sink=sink)

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


def _run_streaming(cmd, *, timeout, cwd, env, shell, display, sink) -> CommandResult:
    """Run with Popen, streaming each merged stdout/stderr line to `sink`."""
    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=cwd,
            env=env,
            shell=shell,
            start_new_session=True,  # own process group: job cancel can killpg
        )
        # register with the active background job (if any) so cancel()
        # can kill this process group — v5.2
        with contextlib.suppress(Exception):
            from suijin.modules.tools.lib.job_registry import _register_proc

            _register_proc(proc)
    except FileNotFoundError as e:
        return CommandResult(display, -1, "", f"command not found: {e}", int((time.time() - start) * 1000))
    except Exception as e:
        return CommandResult(display, -1, "", f"execution fault: {e}", int((time.time() - start) * 1000))

    chunks: list[str] = []

    def _read():
        try:
            for line in proc.stdout:
                chunks.append(line)
                with contextlib.suppress(Exception):
                    sink(line)
        except Exception:
            pass

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    reader.join(timeout)

    if reader.is_alive():
        proc.kill()
        reader.join()
        return CommandResult(
            display, -1, "".join(chunks), f"timed out after {timeout}s", int((time.time() - start) * 1000)
        )

    code = proc.wait()
    return CommandResult(display, code, "".join(chunks), "", int((time.time() - start) * 1000))
