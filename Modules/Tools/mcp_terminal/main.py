"""MCP Terminal — persistent PTY shell session."""
import subprocess, os, time

_shell = None
_shell_cwd = "/tmp"

def _get_shell():
    global _shell
    if _shell is None or _shell.poll() is not None:
        _shell = subprocess.Popen(
            ["/bin/bash", "--norc"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, cwd=_shell_cwd
        )
        time.sleep(0.3)
        _shell.stdout.read(4096)
    return _shell

def mcp_shell_exec(cmd):
    global _shell_cwd
    s = _get_shell()
    try:
        s.stdin.write(cmd + '; echo __EXIT__:$?\n')
        s.stdin.flush()
        time.sleep(0.5)
        out = ""
        while True:
            line = s.stdout.readline()
            if not line:
                break
            if "__EXIT__:" in line:
                break
            out += line
        return (out or "(no output)")[:8000]
    except Exception as e:
        return f"Shell error: {e}"

def mcp_shell_cd(path):
    global _shell_cwd
    _shell_cwd = path
    return mcp_shell_exec(f"cd {path} && pwd")