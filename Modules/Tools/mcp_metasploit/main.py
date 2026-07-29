"""MCP Metasploit — stateful msfconsole via pseudo-terminal."""
import subprocess, os, time, json

_msf_proc = None
_msf_output = []

def _start_msf():
    global _msf_proc
    if _msf_proc is None or _msf_proc.poll() is not None:
        _msf_proc = subprocess.Popen(
            ["msfconsole", "-q", "-x", "setg LogLevel 0"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        time.sleep(2)
        _msf_proc.stdout.read(4096)  # drain banner

def mcp_msf_console(cmd):
    _start_msf()
    try:
        _msf_proc.stdin.write(cmd + "\n")
        _msf_proc.stdin.flush()
        time.sleep(0.5)
        out = _msf_proc.stdout.read(8192)
        return out or "(no output)"
    except Exception as e:
        return f"MSF console error: {e}"

def mcp_msf_session(session_id, cmd):
    return mcp_msf_console(f"sessions -i {session_id}\n{cmd}")