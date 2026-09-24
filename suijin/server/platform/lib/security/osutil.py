"""
suijin/osutil.py
=================
Cross-platform process / file helpers.

Why this exists
---------------
The Blue Team loop and the viewer launcher were written for macOS/Linux and
shell out to Unix-only tools — ``lsof``, ``kill -9``, ``nohup ... &``,
``tail``, ``rm -f`` — and hardcode the ``python3`` interpreter name. None of
those exist on Windows, so the tool could not run there at all.

This module wraps each of those operations behind a single function that
does the right thing on Windows, macOS and Linux. Callers stop emitting raw
shell strings and call these helpers instead.

Implementation notes
--------------------
* Uses ``psutil`` when it is installed (cleanest, fully cross-platform) and
  falls back to per-OS stdlib / subprocess logic when it is not, so the tool
  works out-of-the-box without the extra dependency.
* ``python_exe()`` returns ``sys.executable`` — the interpreter actually
  running Suijin — which is always correct and never assumes ``python3``.
"""

import os
import platform
import signal
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"

try:
    import psutil  # optional, cross-platform process management
except Exception:
    psutil = None


def os_name():
    """Human-readable OS name: 'Windows', 'Darwin' (macOS) or 'Linux'."""
    return platform.system()


def shell_family():
    """Return 'cmd' on Windows, 'bash' elsewhere — used to brief the AI."""
    return "cmd" if IS_WINDOWS else "bash"


def python_exe():
    """The Python interpreter to relaunch with (never the hardcoded 'python3')."""
    return sys.executable or ("python" if IS_WINDOWS else "python3")


def find_pids_on_port(port):
    """Return a list of PIDs (ints) listening on the given TCP port."""
    port = int(port)
    pids = []
    if psutil is not None:
        try:
            for c in psutil.net_connections(kind="inet"):
                if c.laddr and c.laddr.port == port and c.pid:
                    pids.append(c.pid)
            return sorted(set(pids))
        except Exception:
            pass  # fall through to subprocess

    try:
        if IS_WINDOWS:
            # netstat -ano: columns ... Local Address ... PID (last column)
            out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                parts = line.split()
                if (
                    len(parts) >= 5
                    and parts[0].upper() == "TCP"
                    and parts[1].endswith(f":{port}")
                    and parts[3].upper() == "LISTENING"
                    and parts[-1].isdigit()
                ):
                    pids.append(int(parts[-1]))
        else:
            out = subprocess.run(["lsof", "-i", f":{port}", "-t"], capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                if line.strip().isdigit():
                    pids.append(int(line.strip()))
    except Exception:
        pass
    return sorted(set(pids))


def kill_pid(pid):
    """Forcefully terminate a process by PID. Returns True on a clean attempt."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False

    if psutil is not None:
        try:
            psutil.Process(pid).kill()
            return True
        except Exception:
            pass

    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True, text=True, timeout=10)
        else:
            os.kill(pid, signal.SIGKILL)
        return True
    except Exception:
        return False


def launch_background(cmd_list, logfile, cwd=None):
    """
    Start a detached background process, redirecting stdout+stderr to logfile.
    Cross-platform replacement for ``nohup CMD > logfile 2>&1 &``.
    Returns the Popen object (use .pid).
    """
    log = open(logfile, "w", encoding="utf-8", errors="ignore")  # noqa: SIM115 — fd inherited by the detached child
    kwargs = dict(stdout=log, stderr=subprocess.STDOUT)
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    if IS_WINDOWS:
        # Detach so it survives and doesn't open a new console window.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True  # equivalent of nohup
    return subprocess.Popen(cmd_list, **kwargs)


def launch_detached(cmd_list):
    """Fire-and-forget a process with no console window, output discarded."""
    kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(cmd_list, **kwargs)


def tail_file(path, n=30):
    """Return the last n lines of a text file (replacement for ``tail -n``)."""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            return "".join(f.readlines()[-n:])
    except Exception:
        return ""


def remove_file(path):
    """Delete a file if it exists (replacement for ``rm -f``). Never raises."""
    try:
        Path(path).unlink(missing_ok=True)
        return True
    except Exception:
        return False
