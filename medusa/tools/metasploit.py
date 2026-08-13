"""Metasploit integration: RPC daemon and msfconsole fallback."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import xmlrpc.client

from medusa.core.constants import METASPLOIT_RPC_PORT

from .runtime import BASE_DIR, truncate


def _msf_rpc_connect(config):
    """Connect to an msfrpcd daemon and return (proxy, token) or (None, error)."""
    host = config.get("metasploit_rpc_host", "127.0.0.1")
    port = int(config.get("metasploit_rpc_port", METASPLOIT_RPC_PORT))
    password = os.environ.get("MSF_RPC_PASSWORD", "")
    if not password:
        return None, "Metasploit RPC password not set in config (metasploit_rpc_password)."
    try:
        proxy = xmlrpc.client.ServerProxy(
            f"http://{host}:{port}/api/",
            allow_none=True,
            use_datetime=True,
        )
        auth = proxy.auth.login("msf", password)
        if auth.get("result") == "success":
            return proxy, auth.get("token")
        else:
            return None, f"Metasploit RPC auth failed: {auth.get('error', 'unknown')}"
    except Exception as e:
        return None, f"Metasploit RPC connection error: {e}"


def _msf_console_fallback(cmd, config=None):
    """Fallback: run msfconsole -q -x with a command and return output."""
    import tempfile, time
    out_path = BASE_DIR / f".msf_out_{int(time.time())}.txt"
    full_cmd = f"msfconsole -q -x {shlex.quote(cmd)} -o {shlex.quote(str(out_path))}"
    try:
        subprocess.run(full_cmd, shell=True, timeout=60,
                       capture_output=True, text=True)
        if out_path.exists():
            text = out_path.read_text(encoding="utf-8", errors="replace")
            out_path.unlink(missing_ok=True)
            return truncate(text.strip() or "(no output)")
        return "(no output written)"
    except subprocess.TimeoutExpired:
        out_path.unlink(missing_ok=True)
        return "Error: msfconsole timed out after 60 seconds."
    except FileNotFoundError:
        return "Error: msfconsole not found. Install Metasploit or check PATH."
    except Exception as e:
        out_path.unlink(missing_ok=True)
        return f"Error: msfconsole execution failed: {e}"


def msf_check(config):
    """Probe whether Metasploit is available (RPC first, then console)."""
    proxy, token = _msf_rpc_connect(config)
    if proxy is not None:
        try:
            version = proxy.core.version(token)
            return (
                f"Metasploit RPC connected.\n"
                f"Version: {version}\n"
                f"RPC: {config.get('metasploit_rpc_host', '127.0.0.1')}:"
                f"{config.get('metasploit_rpc_port', METASPLOIT_RPC_PORT)}"
            )
        except Exception as e:
            return f"Metasploit RPC connected but core.version failed: {e}"

    # Fallback: check if msfconsole exists
    try:
        r = subprocess.run("which msfconsole", shell=True, capture_output=True,
                           text=True, timeout=5)
        if r.stdout.strip():
            return (
                f"Metasploit available via msfconsole at: {r.stdout.strip()}\n"
                "No RPC daemon detected. Use msf_command with console fallback.\n"
                "To enable RPC, set metasploit_rpc_host/port/password in config."
            )
    except Exception:
        pass
    return (
        "Metasploit NOT detected.\n"
        "Install from: https://www.metasploit.com/\n"
        "Or start msfrpcd for RPC access."
    )


def msf_command(cmd, config):
    """Run a raw Metasploit command via RPC or msfconsole fallback."""
    proxy, token = _msf_rpc_connect(config)
    if proxy is not None:
        try:
            # Create a temporary console for this command
            console_info = proxy.console.create(token)
            cid = console_info.get("id")
            if not cid:
                return f"Error: failed to create console — {console_info}"
            # Write the command
            proxy.console.write(token, cid, cmd + "\n")
            # Wait a beat then read
            import time
            time.sleep(1.5)
            output = proxy.console.read(token, cid)
            data = output.get("data", "")
            # Destroy the console
            proxy.console.destroy(token, cid)
            return truncate(data.strip() or "(no output)")
        except Exception as e:
            return f"Error: RPC command failed — {e}"

    # Fallback to msfconsole
    return _msf_console_fallback(cmd, config)


def msf_run(module, payload, options, config):
    """Configure and execute a Metasploit module via RPC.

    Args:
        module:  e.g. "exploit/multi/handler"
        payload: e.g. "windows/meterpreter/reverse_tcp" (optional for aux)
        options: dict of module options, e.g. {"RHOSTS": "10.0.0.1"}
        config:  app config for RPC connection info
    """
    proxy, token = _msf_rpc_connect(config)
    if proxy is None:
        # Build a resource script as fallback
        lines = [f"use {module}"]
        if isinstance(options, dict):
            for k, v in options.items():
                lines.append(f"set {k} {v}")
        if payload:
            lines.append(f"set PAYLOAD {payload}")
        lines.append("run -j")
        return _msf_console_fallback("; ".join(lines))

    try:
        # Set payload if provided
        if payload:
            proxy.module.execute(token, "auxiliary" if "/aux" in module or module.startswith("aux")
                                 else "exploit",
                                 module, {"PAYLOAD": payload})

        # Build options dict
        opts = dict(options) if isinstance(options, dict) else {}

        # Determine module type from path
        mtype = "auxiliary"
        if module.startswith("exploit") or "/exploit" in module:
            mtype = "exploit"
        elif module.startswith("post") or "/post" in module:
            mtype = "post"
        elif module.startswith("payload") or "/payload" in module:
            mtype = "payload"
        elif module.startswith("nop") or "/nop" in module:
            mtype = "nop"
        elif module.startswith("encoder") or "/encoder" in module:
            mtype = "encoder"

        # Execute
        result = proxy.module.execute(token, mtype, module, opts)
        # Return structured result
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error: msf_run failed — {e}"


def msf_sessions(action, session_id, config):
    """Manage active Metasploit sessions.

    action: "list" | "interact" | "kill"
    session_id: required for interact/kill
    """
    proxy, token = _msf_rpc_connect(config)
    if proxy is None:
        if action == "list":
            return _msf_console_fallback("sessions -l")
        elif action == "kill":
            return _msf_console_fallback(f"sessions -k {session_id}")
        else:
            return "Error: RPC required for session interaction. Start msfrpcd."

    try:
        if action == "list":
            sessions = proxy.session.list(token)
            return json.dumps(sessions, indent=2, default=str)
        elif action == "kill" and session_id:
            result = proxy.session.stop(token, session_id)
            return json.dumps(result, indent=2, default=str)
        elif action == "interact" and session_id:
            # Read recent output from a session
            result = proxy.session.read(token, session_id)
            return json.dumps(result, indent=2, default=str)
        else:
            return f"Error: msf_sessions needs action=list|interact|kill (got '{action}')"
    except Exception as e:
        return f"Error: msf_sessions failed — {e}"
