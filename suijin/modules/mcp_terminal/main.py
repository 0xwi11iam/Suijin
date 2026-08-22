"""MCP Terminal — persistent PTY shell session (v5.2: pty + marker read).

v5.2 fix: the old implementation slept 0.5s then read whatever was
buffered on a PIPE — anything slower (nmap, pip, compilation) silently
lost output past the window, and readline() on a pipe with no data
blocks forever (the poll loop never ran). The real fix is a true PTY
(what the tool was always named for): select()-able, non-blocking,
and bash treats it as a terminal so it flushes properly.
"""

import os
import pty
import select
import subprocess
import time

_master = None
_proc = None
_cwd = "/tmp"
_TIMEOUT = 120.0  # hard cap


def _get_session():
    global _master, _proc
    if _proc is not None and _proc.poll() is not None:
        # bash died — clean up and restart
        try:
            os.close(_master)
        except OSError:
            pass
        _master = _proc = None
    if _master is None:
        _master, slave = pty.openpty()
        _proc = subprocess.Popen(
            ["/bin/bash", "--norc"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            cwd=_cwd,
        )
        os.close(slave)
        time.sleep(0.3)
        _drain()
    return _master


def _drain():
    """Non-blocking drain of any buffered output."""
    while True:
        r, _, _ = select.select([_master], [], [], 0)
        if not r:
            return
        try:
            if not os.read(_master, 65536):
                return
        except OSError:
            return


def mcp_shell_exec(cmd):
    global _cwd
    master = _get_session()
    marker = f"__SUIJIN_M_{time.time_ns()}__"
    try:
        os.write(master, f"{cmd}; echo {marker}\n".encode())
    except OSError as e:
        return f"Shell error (write): {e}"

    out = b""
    deadline = time.monotonic() + _TIMEOUT
    while time.monotonic() < deadline:
        r, _, _ = select.select([master], [], [], 0.5)
        if not r:
            continue
        try:
            chunk = os.read(master, 65536)
        except OSError:
            break
        if not chunk:
            break
        m = marker.encode()
        if m in chunk:
            out += chunk[: chunk.index(m)]
            break
        out += chunk

    text = out.decode(errors="ignore").strip()
    if time.monotonic() >= deadline:
        text += "\n(timeout: output may be truncated)"
    return (text or "(no output)")[:8000]


def mcp_shell_cd(path):
    global _cwd
    _cwd = path
    return mcp_shell_exec(f"cd {path} && pwd")


def mcp_shell_close():
    global _master, _proc
    if _proc is not None:
        _proc.kill()
        _proc = None
    if _master is not None:
        try:
            os.close(_master)
        except OSError:
            pass
        _master = None
