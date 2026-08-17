"""Operational mode enforcement — dispatch-level backstop for safety modes.

`prompts/base.py` injects the mode constraints into the system prompt; this
module enforces the same modes at the `route_tool` chokepoint so a prompt
injection or model misread cannot bypass them:

- mode_hitl      → recon/read-only tools only; exploits must be documented
                   for the human operator, never executed.
- mode_guardrail → no filesystem-mutating or process-killing commands.

Both flags are read from the loaded config (values may be bools after Pydantic
validation or strings straight from config.json, so coercion handles both).
"""
from __future__ import annotations

import re


def _flag(config: dict | None, key: str) -> bool:
    val = (config or {}).get(key, False)
    return str(val).strip().lower() in ("1", "true", "yes", "on")


# Read-only / recon tools permitted in HITL mode. Everything else (exploits,
# metasploit, file writes, self-modification) is blocked.
_HITL_ALLOWED_TOOLS = {
    "search_kb", "search_cve", "check_knowledge", "record_finding",
    "write_note", "web_search", "read_file", "http_request",
    "recon_chain", "msf_check", "diff_response", "rate_limit_check",
    "rate_limit_all", "attack_tree", "generate_report", "claim_flag",
    "job_status", "job_wait", "job_output", "job_list", "job_cancel",
    "list_skills", "list_own_files",
    "execute_terminal",  # command-level allowlist applied below
}

# Recon / fingerprinting binaries + harmless local readers. Deliberately
# excludes interpreters (python3, sh) so HITL cannot be bypassed with -c.
_HITL_ALLOWED_BINARIES = {
    "nmap", "masscan", "gobuster", "ffuf", "feroxbuster", "dirb", "dirsearch",
    "nikto", "whatweb", "sslscan", "nuclei", "wafw00f",
    "amass", "subfinder", "httpx", "katana",
    "dig", "nslookup", "host", "whois",
    "curl", "wget", "echo", "cat", "ls", "grep", "head", "tail", "wc",
    "sort", "uniq",
}

# Binaries forbidden in guardrail mode: mutate the filesystem, kill
# processes, or alter system state.
_GUARDRAIL_BLOCKED_BINARIES = {
    "rm", "mv", "chmod", "chown", "kill", "pkill", "killall",
    "shutdown", "reboot", "halt", "telinit", "systemctl", "service",
    "mkfs", "fdisk", "dd",
}


def check_mode_restrictions(tool_name: str, args: dict | None, config: dict | None) -> str | None:
    """Return a block message if the call violates an active mode, else None.

    This is a backstop, not the only line of defense — the same modes are
    described in the system prompt (prompts/base.py).
    """
    args = args or {}
    if _flag(config, "mode_hitl"):
        blocked = _hitl_check(tool_name, args)
        if blocked:
            return blocked
    if _flag(config, "mode_guardrail"):
        blocked = _guardrail_check(tool_name, args)
        if blocked:
            return blocked
    return None


def _segment_binaries(cmd: str) -> list[str]:
    """First token of every sub-command in a shell line.

    Splits on `;`, `|`, `&&`, `||` so each segment's binary is checked —
    defeats `nmap -sV target; rm -rf /` style trailing-payload evasion.
    """
    binaries = []
    for segment in re.split(r";|\|\||&&|\|", cmd):
        tokens = segment.strip().split()
        if tokens:
            binaries.append(tokens[0])
    return binaries


def _hitl_check(tool_name: str, args: dict) -> str | None:
    if tool_name not in _HITL_ALLOWED_TOOLS:
        return (
            f"BLOCKED by HITL mode (recon only): tool '{tool_name}' is not read-only. "
            "Do NOT execute exploits. Write detailed exploitation instructions for the "
            "human operator instead (exact command, expected output, risk level)."
        )
    if tool_name == "execute_terminal":
        cmd = str(args.get("cmd") or args.get("command") or "")
        binaries = _segment_binaries(cmd)
        if not binaries:
            return "BLOCKED by HITL mode (recon only): empty or unparsable command."
        offenders = [b for b in binaries if b not in _HITL_ALLOWED_BINARIES]
        if offenders:
            return (
                f"BLOCKED by HITL mode (recon only): '{offenders[0]}' is not a recon binary. "
                f"Allowed: {', '.join(sorted(_HITL_ALLOWED_BINARIES))}. "
                "Document the exact command for the operator instead of running it."
            )
    return None


def _guardrail_check(tool_name: str, args: dict) -> str | None:
    if tool_name not in ("execute_terminal", "msf_command", "msf_run"):
        return None
    cmd = str(args.get("cmd") or args.get("command") or args.get("module") or "")
    offenders = [b for b in _segment_binaries(cmd) if b in _GUARDRAIL_BLOCKED_BINARIES]
    if offenders:
        return (
            f"BLOCKED by guardrail mode: '{offenders[0]}' deletes/moves files or kills "
            f"processes. Command was: {cmd[:200]}"
        )
    return None
