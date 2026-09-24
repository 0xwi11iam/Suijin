"""Persistent shell sessions — state that survives between commands.

One-shot execute_terminal loses cwd, env vars and shell state on every
call; local-device work (privesc recon, deep directory walks, activated
toolchains) needs a REAL session: cd somewhere, export something, keep
going. Sessions are plain subprocess shells (no pty — interactive
password prompts are explicitly out of scope; non-interactive flags
like `sudo -n` are the doctrine), drained with an end-marker protocol
under a deadline.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time
import uuid

_MAX_SESSIONS = 4
_IDLE_TTL = 1800  # reap after 30 min untouched
_OUTPUT_CAP = 60000

_SESSIONS: dict[str, dict] = {}
_lock = threading.Lock()


# markers must be astronomically unlikely to appear in output
def _marker() -> str:
    return f"__SJSNAP_{uuid.uuid4().hex[:12]}__"


def _reap_locked(now: float) -> None:
    for sid in list(_SESSIONS):
        s = _SESSIONS[sid]
        if now - s["last"] > _IDLE_TTL:
            _stop_locked(sid)


def _stop_locked(sid: str) -> None:
    s = _SESSIONS.pop(sid, None)
    if not s:
        return
    with contextlib.suppress(Exception):
        s["proc"].stdin.close()
    try:
        s["proc"].terminate()
        s["proc"].wait(timeout=2)
    except Exception:  # noqa: BLE001
        with contextlib.suppress(Exception):
            s["proc"].kill()


def shell_start(shell: str = "", cwd: str = "") -> str:
    """Start a persistent shell session. Returns the session id."""
    try:
        with _lock:
            _reap_locked(time.time())
            if len(_SESSIONS) >= _MAX_SESSIONS:
                return f"Error: {_MAX_SESSIONS} shell sessions already open — shell_stop one first (shell_list shows them)."
        exe = shell or ("/bin/zsh" if os.path.exists("/bin/zsh") else "/bin/bash")
        from suijin.server.confinement import executor_env, wrap_exec

        env = executor_env()
        env.update({"PS1": "", "PROMPT_COMMAND": ""})
        shell_argv, jailed = wrap_exec([exe, "-l"])
        try:
            proc = subprocess.Popen(
                shell_argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(env["HOME"]),
                env=env,
            )
        except (OSError, ValueError):
            # primary argv (possibly sandbox-wrapped) failed to spawn —
            # degrade to a plain /bin/sh (env confinement still applies)
            proc = subprocess.Popen(
                ["/bin/sh"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(env["HOME"]),
                env=env,
            )
        sid = "sh_" + uuid.uuid4().hex[:8]
        with _lock:
            _SESSIONS[sid] = {"proc": proc, "last": time.time(), "shell": exe, "born": time.time()}
        note = " [jailed]" if jailed else ""
        return (
            f"session {sid} ({exe}) started{note}. shell_send commands to it — cwd, env and "
            "shell state PERSIST between sends. Non-interactive doctrine only "
            "(sudo -n, no password prompts: sessions have no tty). shell_stop closes it."
        )
    except Exception as e:  # noqa: BLE001 — a tool never raises
        return f"Error: shell_start failed: {e}"


def shell_send(session_id: str = "", cmd: str = "", timeout: int = 30) -> str:
    """Run a command INSIDE a session — cwd/env persist. Output returns
    when the command finishes (or the timeout truncates it)."""
    try:
        sid = str(session_id or "").strip()
        cmd = str(cmd or "").strip()
        if not sid or sid not in _SESSIONS:
            return f"Error: no such session ({sid!r}) — shell_start one, or shell_list for open ids."
        if not cmd:
            return "Error: no command."
        s = _SESSIONS[sid]
        proc: subprocess.Popen = s["proc"]
        if proc.poll() is not None:
            with _lock:
                _SESSIONS.pop(sid, None)
            return f"Error: session {sid} died (exit {proc.returncode}) — shell_start a fresh one."

        mark = _marker()
        # the wrapped command: run, then emit the marker + exit status so
        # the reader knows exactly where this command's output ends
        wrapped = f"{{ {cmd}\n}}; echo {mark}_$?\n"
        try:
            proc.stdin.write(wrapped.encode())
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            return f"Error: session {sid} is not accepting input (dead shell?) — shell_start a fresh one."

        out = bytearray()
        deadline = time.time() + max(3, min(int(timeout or 30), 300))
        tail = ""
        while time.time() < deadline:
            import select

            ready, _, _ = select.select([proc.stdout], [], [], 0.3)
            if ready:
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                out += chunk
                tail = bytes(out[-160:]).decode("utf-8", "replace")
                if f"{mark}_" in tail:
                    break
        s["last"] = time.time()
        text = out.decode("utf-8", "replace")
        # cut everything from the marker; surface the exit status
        status = "?"
        if f"{mark}_" in text:
            pre, _, post = text.partition(f"{mark}_")
            text = pre
            status = post.strip().split("\n", 1)[0][:6] or "0"
        text = text.strip()[:_OUTPUT_CAP]
        if not text:
            text = f"(no output, exit {status})"
        return f"[exit {status}]\n{text}"
    except Exception as e:  # noqa: BLE001
        return f"Error: shell_send failed: {e}"


def shell_stop(session_id: str = "") -> str:
    try:
        sid = str(session_id or "").strip()
        with _lock:
            if sid == "all":
                n = len(_SESSIONS)
                for k in list(_SESSIONS):
                    _stop_locked(k)
                return f"stopped {n} session(s)"
            if sid not in _SESSIONS:
                return f"Error: no such session ({sid!r})"
            _stop_locked(sid)
            return f"session {sid} closed"
    except Exception as e:  # noqa: BLE001
        return f"Error: shell_stop failed: {e}"


def shell_list() -> str:
    try:
        with _lock:
            now = time.time()
            rows = [
                f"  {sid} {s['shell']} (idle {int(now - s['last'])}s, pid {s['proc'].pid})"
                for sid, s in sorted(_SESSIONS.items())
            ]
        return ("open shell sessions:\n" + "\n".join(rows)) if rows else "no open shell sessions (shell_start one)"
    except Exception as e:  # noqa: BLE001
        return f"Error: shell_list failed: {e}"
