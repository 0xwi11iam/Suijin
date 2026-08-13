"""Medusa tool dispatcher.

This module is the public routing surface for every tool. The implementations
live in focused sibling modules; this file assembles the route table and
re-exports the public API for backwards compatibility.

Layout:
  runtime.py      shared state (session, proxy, paths, helpers)
  terminal.py     execute_terminal
  http_tools.py   http_request, apply_patch, read_file, write_file
  metasploit.py   msf_* integration
  intel.py        search_cve, search_kb, knowledge graph, write_note
  reporting.py    payload/diff/rate-limit/attack-tree/report wrappers
  jobs.py         background job management
  aux_tools.py    web search, self-improvement, pip install
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
import subprocess
import threading
import time
import xmlrpc.client
from pathlib import Path

import requests
import urllib3

from medusa.modules.loader import get_module_tools

# Re-exported for backwards compatibility — these names lived on dispatch.py
# before the split and external callers still import them from here.
from medusa.tools.guardrails import _BLOCKED_PATTERNS, confirm_global_action, is_dangerous
from medusa.tools.workspace import WORKSPACE_DIR, resolve_workspace_path

# ── Re-export the public tool surface ─────────────────────────────────
from medusa.tools.runtime import (
    AI_SERVICE_ENDPOINTS,
    BASE_DIR,
    DB_PATH,
    MCP_SERVERS,
    PROJECT_DIR,
    _job_lock,
    _jobs,
    _recon_state,
    fingerprint_ai_response,
    get_proxy,
    get_server_for_tool,
    global_session,
    reset_recon_state,
    set_proxy,
    truncate,
)
from medusa.tools.terminal import execute_terminal
from medusa.tools.http_tools import apply_patch, http_request, read_file, write_file
from medusa.tools.metasploit import (
    _msf_console_fallback,
    _msf_rpc_connect,
    msf_check,
    msf_command,
    msf_run,
    msf_sessions,
)
from medusa.tools.intel import (
    NVD_BASE,
    NOTES_DIR,
    _extract_cvss,
    _is_kev,
    check_knowledge,
    record_finding,
    search_cve,
    search_kb,
    write_note,
)
from medusa.tools.reporting import (
    _attack_tree,
    _diff_resp,
    _gen_report,
    _payload_gen,
    _rate_all,
    _rate_check,
)
from medusa.tools.jobs import (
    _job_cancel,
    _job_list,
    _job_output,
    _job_status,
    _job_wait,
)
from medusa.tools.aux_tools import (
    _edit_skill,
    _list_own_files,
    _list_skills,
    _pip_install,
    _web_search,
    _write_tool,
)


def _build_routes(config):
    routes = {
        "execute_terminal": lambda a: execute_terminal(a.get("cmd") or a.get("command"), timeout=int(a.get("timeout", 30))),
        "search_kb": lambda a: search_kb(a.get("keyword")),
        "http_request": lambda a: http_request(a.get("method", "GET"), a.get("url"), a.get("headers"), a.get("body")),
        "read_file": lambda a: read_file(a.get("file_path", "")),
        "write_file": lambda a: write_file(a.get("file_path", ""), a.get("content", "")),
        "apply_patch": lambda a: apply_patch(a.get("vulnerability"), a.get("file_path", "lab.py")),
        "claim_flag": lambda a: f"OBJECTIVE MET: {a.get('flag')}",
        # Metasploit tools
        "msf_check": lambda a: msf_check(config),
        "msf_command": lambda a: msf_command(a.get("cmd") or a.get("command"), config),
        "msf_run": lambda a: msf_run(a.get("module"), a.get("payload"), a.get("options") or {}, config),
        "msf_sessions": lambda a: msf_sessions(a.get("action", "list"), a.get("id"), config),
        # CVE / vulnerability intelligence
        "search_cve": lambda a: search_cve(a.get("software"), config, version=a.get("version"), limit=int(a.get("limit", 5))),
        # Oracle / knowledge graph
        "check_knowledge": lambda a: check_knowledge(a.get("target"), payload=a.get("payload"), config=config),
        "record_finding": lambda a: record_finding(a.get("target"), a.get("finding_type"), a.get("rule"), evidence=a.get("evidence", ""), config=config),
        # Note-taking
        "write_note": lambda a: write_note(a.get("content", ""), success=a.get("success", True), category=a.get("category", "general"), engagement=a.get("engagement"), config=config),
        # Web search & self-improvement
        "web_search": lambda a: _web_search(a.get("query", ""), int(a.get("max_results", 5))),
        "edit_skill": lambda a: _edit_skill(a.get("skill_name", ""), a.get("new_content", "")),
        "write_tool": lambda a: _write_tool(a.get("tool_name", ""), a.get("code", "")),
        "list_skills": lambda a: _list_skills(),
        "list_own_files": lambda a: _list_own_files(),
        "pip_install": lambda a: _pip_install(a.get("package", "")),
        # Background job management
        "job_status": lambda a: _job_status(a.get("job_id", "")),
        "job_wait": lambda a: _job_wait(a.get("job_id", ""), a.get("timeout", 60)),
        "job_output": lambda a: _job_output(a.get("job_id", "")),
        "job_list": lambda a: _job_list(),
        "job_cancel": lambda a: _job_cancel(a.get("job_id", "")),
        # Analysis & reporting
        "payload_generate": lambda a: _payload_gen(a.get("vuln_type", ""), a.get("framework", "")),
        "diff_response": lambda a: _diff_resp(a.get("baseline", ""), a.get("injected", ""), a.get("sensitivity", "medium")),
        "rate_limit_check": lambda a: _rate_check(a.get("endpoint", "")),
        "rate_limit_all": lambda a: _rate_all(),
        "attack_tree": lambda a: _attack_tree(a.get("trace_json", "")),
        "generate_report": lambda a: _gen_report(a.get("engagement", ""), a.get("trace_json", ""), a.get("findings_json", "")),
        # deploy_subagent is an ACTION, not a tool. If the AI accidentally uses
        # it as a tool_name, show EXACTLY how to fix it so it self-corrects.
        "deploy_subagent": lambda a: (
            "WRONG FORMAT. deploy_subagent is an ACTION type, not a tool_name.\n"
            "You used: {\"action\": \"use_tool\", \"tool_name\": \"deploy_subagent\", ...}\n"
            "USE INSTEAD: {\"action\": \"deploy_subagent\", \"subagent_task\": \"your task\", \"thought\": \"...\"}\n"
            "Separate multiple tasks with || for parallel execution.\n"
            "Example: {\"action\": \"deploy_subagent\", \"subagent_task\": \"SQLi test on /login || XSS test on /search\", \"thought\": \"parallel attacks\"}"
        ),
    }
    # Inject module tools dynamically
    for t_name, t_func in get_module_tools().items():
        routes[t_name] = lambda a, f=t_func: f(**a)

    return routes


def list_route_tools():
    """Return the names of every dispatchable tool (explicit + module tools)."""
    return sorted(_build_routes(None).keys())


def route_tool(tool_name, args, config):
    if args is None:
        args = {}
    routes = _build_routes(config)

    # ── FREEDOM: no phase gating. All tools always available. ──

    # Track recon actions (for informational purposes only)
    RECON_TOOLS = {
        "execute_terminal", "http_request", "search_cve", "search_kb",
        "read_file", "check_knowledge",
    }
    if tool_name in RECON_TOOLS:
        _recon_state["exploration_count"] = _recon_state.get("exploration_count", 0) + 1

    if tool_name in routes:
        try:
            return routes[tool_name](args)
        except Exception as e:
            return f"Routing Error: {str(e)}"
    return f"Invalid Tool: {tool_name}"


def get_tool_catalog():
    """Return a formatted catalog of ALL available tools for the AI's system prompt.

    Dynamically includes core tools, Metasploit, CVE search, Oracle, notes,
    and any loaded module tools. Called from redteamer to build the prompt.
    """
    from medusa.modules.loader import get_loaded_modules

    catalog = ""

    # ── MUST-USE TOOLS (these are NOT optional) ─────────────────────
    catalog += """##  MANDATORY TOOLS — Use These Every Turn
- **write_note** — MANDATORY after EVERY action. Log what you did, what happened, and what you learned. Categories: recon, exploit, cve, blocked, finding, progress, complete. Your audit trail and final report depend on these notes. DO NOT SKIP.
  ```json
  {"tool": "write_note", "args": {"content": "Tested SQLi on /login with payload ' OR 1=1 --. Login bypass confirmed. Gained admin session.", "success": true, "category": "finding", "engagement": "target-name"}}
  ```
- **check_knowledge** — QUERY THE KNOWLEDGE GRAPH before EVERY payload attempt. It stores verified blocked patterns, WAF rules, and successful exploit vectors. Stop wasting cycles on known-blocked payloads.
  ```json
  {"tool": "check_knowledge", "args": {"target": "TARGET_HOST"}}
  ```
- **record_finding** — WRITE TO THE KNOWLEDGE GRAPH after EVERY confirmed result. SQLi works? Record it. WAF blocked something? Record it. CVE confirmed? Record it. This prevents duplicate work and builds institutional knowledge.
  ```json
  {"tool": "record_finding", "args": {"target": "TARGET", "finding_type": "verified_cve", "rule": "CVE-2021-41773 path traversal works on /cgi-bin/.%2e/%2e%2e/etc/passwd", "evidence": "Got /etc/passwd contents in response"}}
  ```
- **generate_report** — MANDATORY at engagement end. Creates detailed Markdown report with all findings, attack chains, Mermaid diagrams. Call BEFORE complete/claim_flag.
  ```json
  {"tool": "generate_report", "args": {"engagement": "target-name"}}
  ```

## Core Tools
- **execute_terminal** — Run ANY shell command. Use this for CLI tools: nmap, gobuster, ffuf, nikto, sqlmap, hydra, john, enum4linux, dirb, masscan, and any other pentesting tool installed on the system. Prefer dedicated CLI tools over raw curl/http_request for scanning and brute-forcing.
  ```json
  {"tool": "execute_terminal", "args": {"cmd": "gobuster dir -u http://TARGET -w /usr/share/wordlists/dirb/common.txt"}}
  {"tool": "execute_terminal", "args": {"cmd": "nmap -sV -sC TARGET"}}
  ```
- **http_request** — Raw HTTP requests with full browser emulation. Use for manual web testing, not for scanning (use gobuster/nmap via execute_terminal instead).
  ```json
  {"tool": "http_request", "args": {"method": "GET", "url": "http://TARGET/page"}}
  ```
- **read_file** — Read any file on the system.
  ```json
  {"tool": "read_file", "args": {"file_path": "/etc/hosts"}}
  ```
- **write_file** — Write files (scripts, payloads, notes). Defaults to medusa_agent/ for relative paths.
  ```json
  {"tool": "write_file", "args": {"file_path": "scripts/exploit.py", "content": "#!/usr/bin/env python3\\n..."}}
  ```
- **search_kb** — Search the local knowledge base.
  ```json
  {"tool": "search_kb", "args": {"keyword": "SQL injection"}}
  ```
- **apply_patch** — Patch vulnerabilities in the target lab application.
  ```json
  {"tool": "apply_patch", "args": {"vulnerability": "sqli"}}
  ```
- **claim_flag** — Signal objective complete.
  ```json
  {"tool": "claim_flag", "args": {"flag": "flag{...}"}}
  ```

## Metasploit
- **msf_check** — Verify Metasploit availability.
  ```json
  {"tool": "msf_check", "args": {}}
  ```
- **msf_command** — Run raw msfconsole commands.
  ```json
  {"tool": "msf_command", "args": {"cmd": "search eternalblue"}}
  ```
- **msf_run** — Execute exploit/auxiliary/post modules.
  ```json
  {"tool": "msf_run", "args": {"module": "exploit/multi/handler", "payload": "windows/meterpreter/reverse_tcp", "options": {"LHOST": "10.0.0.5", "LPORT": "4444"}}}
  ```
- **msf_sessions** — Manage sessions.
  ```json
  {"tool": "msf_sessions", "args": {"action": "list"}}
  ```

## Intelligence
- **search_cve** — Query NVD for CVEs by software+version.
  ```json
  {"tool": "search_cve", "args": {"software": "apache httpd", "version": "2.4.49", "limit": 5}}
  ```
- **check_knowledge** — Query the knowledge graph before generating payloads.
  ```json
  {"tool": "check_knowledge", "args": {"target": "TARGET"}}
  ```
- **record_finding** — Persist verified findings.
  ```json
  {"tool": "record_finding", "args": {"target": "TARGET", "finding_type": "blocks", "rule": "' OR 1=1", "evidence": "WAF 403"}}
  ```
- **write_note** — Log engagement progress.
  ```json
  {"tool": "write_note", "args": {"content": "Progress update...", "success": true, "category": "progress", "engagement": "target-name"}}
  ```

## Creative Freedom Tools
- **web_search** — Search the internet for exploit techniques, CVE details, documentation.
  ```json
  {"tool": "web_search", "args": {"query": "apache 2.4.49 CVE exploit", "max_results": 5}}
  ```
- **pip_install** — Install Python packages the agent needs (requests, pwntools, etc).
  ```json
  {"tool": "pip_install", "args": {"package": "requests"}}
  ```
- **edit_skill** — Improve your own hacking methodology by editing skill prompts.
  ```json
  {"tool": "edit_skill", "args": {"skill_name": "sql_injection", "new_content": "..."}}
  ```
- **write_tool** — Create new Python tools to extend your capabilities.
  ```json
  {"tool": "write_tool", "args": {"tool_name": "my_scanner", "code": "def scan():..."}}
  ```
- **list_skills** — See all attack skills you can edit.
  ```json
  {"tool": "list_skills", "args": {}}
  ```
- **list_own_files** — See all code files you can read and modify.
  ```json
  {"tool": "list_own_files", "args": {}}
  ```

## Background Jobs (parallel execution)
- **job_spawn** happens automatically for slow tools (nmap, gobuster, sqlmap, hydra, ffuf, nikto).
  When you run these via execute_terminal, they return a job_id immediately. You keep working!
- **job_status** — Check status of a background job.
  ```json
  {"tool": "job_status", "args": {"job_id": "abc123"}}
  ```
- **job_wait** — Wait for a job to complete (with timeout).
  ```json
  {"tool": "job_wait", "args": {"job_id": "abc123", "timeout": 60}}
  ```
- **job_output** — Get full output from a completed job.
  ```json
  {"tool": "job_output", "args": {"job_id": "abc123"}}
  ```
- **job_list** — List all running background jobs.
  ```json
  {"tool": "job_list", "args": {}}
  ```
- **job_cancel** — Cancel a running job.
  ```json
  {"tool": "job_cancel", "args": {"job_id": "abc123"}}
  ```
"""

    # Module tools
    modules = get_loaded_modules()
    if modules:
        catalog += "## Module Tools\n"
        for mod_name, mod_data in modules.items():
            manifest = mod_data.get("manifest", {})
            tools = manifest.get("tools", {})
            deps = manifest.get("dependencies", [])
            if tools:
                catalog += f"### {mod_name}"
                if deps:
                    catalog += f" (requires: {', '.join(deps)})"
                catalog += "\n"
                for t_name, t_info in tools.items():
                    desc = t_info.get("description", "")
                    params = t_info.get("parameters", {})
                    param_example = ", ".join(f'"{p}": "..."' for p in params)
                    catalog += f"- **{t_name}** — {desc}\n"
                    catalog += f"  ```json\n"
                    if param_example:
                        catalog += f'  {{"tool": "{t_name}", "args": {{{param_example}}}}}\n'
                    else:
                        catalog += f'  {{"tool": "{t_name}", "args": {{}}}}\n'
                    catalog += f"  ```\n"

    # Strategy reminder
    catalog += """
## Attack Strategy (MUST FOLLOW)
1. **Recon first** — Always start with `execute_terminal` running gobuster/nmap/nikto before manual testing. Never start with raw curl.
2. **CVE before exploit** — `search_cve` after fingerprinting a service. Don't guess.
3. **Knowledge graph before payload** — `check_knowledge` before every new payload.
4. **Verify before claiming** — Confirm exploits with tool-call evidence. No hallucinations.
5. **Log everything** — `write_note` after every significant finding.
6. **Module tools** — Use loaded module tools (above) when applicable instead of reinventing.
"""
    return catalog
