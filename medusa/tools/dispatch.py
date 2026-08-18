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
  kb_tools.py     find_wordlist, kb_stats, suggest_exploit, extract_payloads,
                  wordlist_tool, mine_failures, anonymize_report
  reporting.py    payload/diff/rate-limit/attack-tree/report wrappers
  jobs.py         background job management
  aux_tools.py    web search, self-improvement, pip install
"""

from __future__ import annotations

from medusa.modules.loader import get_module_tools
from medusa.tools.aux_tools import (
    _edit_skill,
    _list_own_files,
    _list_skills,
    _pip_install,
    _web_search,
    _write_tool,
)

# Re-exported for backwards compatibility — these names lived on dispatch.py
# before the split and external callers still import them from here.
# Everything listed in __all__ below is a deliberate re-export: ruff must
# not prune these "unused" imports.
from medusa.tools.guardrails import _BLOCKED_PATTERNS, confirm_global_action, is_dangerous
from medusa.tools.http_tools import apply_patch, http_request, read_file, write_file
from medusa.tools.intel import (
    NOTES_DIR,
    NVD_BASE,
    _extract_cvss,
    _is_kev,
    check_knowledge,
    record_finding,
    search_cve,
    search_kb,
    write_note,
)
from medusa.tools.jobs import (
    _job_cancel,
    _job_list,
    _job_output,
    _job_status,
    _job_wait,
)
from medusa.tools.kb_tools import (
    anonymize_report,
    extract_payloads,
    find_wordlist,
    kb_stats,
    mine_failures,
    suggest_exploit,
    wordlist_tool,
)
from medusa.tools.metasploit import (
    _msf_console_fallback,
    _msf_rpc_connect,
    msf_check,
    msf_command,
    msf_run,
    msf_sessions,
)
from medusa.tools.modes import check_mode_restrictions
from medusa.tools.reporting import (
    _attack_tree,
    _diff_resp,
    _gen_report,
    _payload_gen,
    _rate_all,
    _rate_check,
)

# ── Re-export the public tool surface ─────────────────────────────────
from medusa.tools.runtime import (
    BASE_DIR,
    DB_PATH,
    PROJECT_DIR,
    _job_lock,
    _jobs,
    _recon_state,
    get_proxy,
    global_session,
    reset_recon_state,
    set_proxy,
    truncate,
)
from medusa.tools.terminal import execute_terminal
from medusa.tools.workspace import WORKSPACE_DIR, resolve_workspace_path

__all__ = [
    # guardrails
    "_BLOCKED_PATTERNS",
    "confirm_global_action",
    "is_dangerous",
    # workspace
    "WORKSPACE_DIR",
    "resolve_workspace_path",
    # runtime
    "BASE_DIR",
    "DB_PATH",
    "PROJECT_DIR",
    "_job_lock",
    "_jobs",
    "_recon_state",
    "get_proxy",
    "global_session",
    "reset_recon_state",
    "set_proxy",
    "truncate",
    # terminal / http
    "execute_terminal",
    "apply_patch",
    "http_request",
    "read_file",
    "write_file",
    # metasploit
    "_msf_console_fallback",
    "_msf_rpc_connect",
    "msf_check",
    "msf_command",
    "msf_run",
    "msf_sessions",
    # intel
    "NVD_BASE",
    "NOTES_DIR",
    "_extract_cvss",
    "_is_kev",
    "check_knowledge",
    "record_finding",
    "search_cve",
    "search_kb",
    "write_note",
    # kb toolkit
    "anonymize_report",
    "extract_payloads",
    "find_wordlist",
    "kb_stats",
    "mine_failures",
    "suggest_exploit",
    "wordlist_tool",
    # jobs
    "_job_cancel",
    "_job_list",
    "_job_output",
    "_job_status",
    "_job_wait",
    # reporting
    "_attack_tree",
    "_diff_resp",
    "_gen_report",
    "_payload_gen",
    "_rate_all",
    "_rate_check",
    # aux
    "_edit_skill",
    "_list_own_files",
    "_list_skills",
    "_pip_install",
    "_web_search",
    "_write_tool",
    # routing
    "route_tool",
    "get_tool_catalog",
    "list_route_tools",
]


def _recon_chain_route(target, config, ports=None):
    from medusa.tools.recon import recon_chain

    return recon_chain(target, config=config, ports=ports)


def _build_routes(config):
    routes = {
        "execute_terminal": lambda a: execute_terminal(
            a.get("cmd") or a.get("command"), timeout=int(a.get("timeout", 30))
        ),
        "search_kb": lambda a: search_kb(a.get("keyword"), limit=a.get("limit") or 5),
        # Knowledge-base toolkit (offline)
        "kb_stats": lambda a: kb_stats(),
        "find_wordlist": lambda a: find_wordlist(a.get("keyword"), extract=a.get("extract", True)),
        "suggest_exploit": lambda a: suggest_exploit(a.get("service"), a.get("version", "")),
        "extract_payloads": lambda a: extract_payloads(a.get("keyword"), max_payloads=int(a.get("max_payloads", 10))),
        "wordlist_tool": lambda a: wordlist_tool(
            a.get("action"),
            a.get("files"),
            out=a.get("out", ""),
            min_len=int(a.get("min_len", 1)),
            max_len=int(a.get("max_len", 256)),
        ),
        "mine_failures": lambda a: mine_failures(max_clusters=int(a.get("max_clusters", 5))),
        "anonymize_report": lambda a: anonymize_report(a.get("file_path", "")),
        "http_request": lambda a: http_request(a.get("method", "GET"), a.get("url"), a.get("headers"), a.get("body")),
        "read_file": lambda a: read_file(a.get("file_path", "")),
        "write_file": lambda a: write_file(a.get("file_path", ""), a.get("content", "")),
        "apply_patch": lambda a: apply_patch(a.get("vulnerability"), a.get("file_path", "lab.py")),
        "claim_flag": lambda a: f"OBJECTIVE MET: {a.get('flag')}",
        # Recon orchestration
        "recon_chain": lambda a: _recon_chain_route(a.get("target"), config, a.get("ports")),
        # Metasploit tools
        "msf_check": lambda a: msf_check(config),
        "msf_command": lambda a: msf_command(a.get("cmd") or a.get("command"), config),
        "msf_run": lambda a: msf_run(a.get("module"), a.get("payload"), a.get("options") or {}, config),
        "msf_sessions": lambda a: msf_sessions(a.get("action", "list"), a.get("id"), config),
        # CVE / vulnerability intelligence
        "search_cve": lambda a: search_cve(
            a.get("software"), config, version=a.get("version"), limit=int(a.get("limit", 5))
        ),
        # Oracle / knowledge graph
        "check_knowledge": lambda a: check_knowledge(a.get("target"), payload=a.get("payload"), config=config),
        "record_finding": lambda a: record_finding(
            a.get("target"), a.get("finding_type"), a.get("rule"), evidence=a.get("evidence", ""), config=config
        ),
        # Note-taking
        "write_note": lambda a: write_note(
            a.get("content", ""),
            success=a.get("success", True),
            category=a.get("category", "general"),
            engagement=a.get("engagement"),
            config=config,
        ),
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
        "diff_response": lambda a: _diff_resp(
            a.get("baseline", ""), a.get("injected", ""), a.get("sensitivity", "medium")
        ),
        "rate_limit_check": lambda a: _rate_check(a.get("endpoint", "")),
        "rate_limit_all": lambda a: _rate_all(),
        "attack_tree": lambda a: _attack_tree(a.get("trace_json", "")),
        "generate_report": lambda a: _gen_report(
            a.get("engagement", ""), a.get("trace_json", ""), a.get("findings_json", "")
        ),
        # deploy_subagent is an ACTION, not a tool. If the AI accidentally uses
        # it as a tool_name, show EXACTLY how to fix it so it self-corrects.
        "deploy_subagent": lambda a: (
            "WRONG FORMAT. deploy_subagent is an ACTION type, not a tool_name.\n"
            'You used: {"action": "use_tool", "tool_name": "deploy_subagent", ...}\n'
            'USE INSTEAD: {"action": "deploy_subagent", "subagent_task": "your task", "thought": "..."}\n'
            "Separate multiple tasks with || for parallel execution.\n"
            'Example: {"action": "deploy_subagent", "subagent_task": "SQLi test on /login || XSS test on /search", "thought": "parallel attacks"}'
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

    # Safety-mode backstop (mode_hitl / mode_guardrail). The modes are also
    # described in the system prompt; this makes them impossible to bypass.
    blocked = check_mode_restrictions(tool_name, args, config)
    if blocked:
        return blocked

    # ── FREEDOM: no phase gating. All tools always available. ──

    # Track recon actions (for informational purposes only)
    RECON_TOOLS = {
        "execute_terminal",
        "http_request",
        "search_cve",
        "search_kb",
        "read_file",
        "check_knowledge",
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
"""

    # Knowledge base — feature-gated: only advertised when the operator has
    # built it with `medusa pull kb`. Otherwise listed as disabled below.
    from medusa.kb import kb_status

    _kb = kb_status()
    if _kb:
        per = ", ".join(f"{k} {v:,}" for k, v in sorted(_kb.get("per_source", {}).items()))
        catalog += f"""- **search_kb** — Full-text search the local knowledge base ({_kb["docs"]:,} docs: {per}). BM25-ranked results with snippets. Optional `source:<name>` filter (e.g. keyword "source:gtfobins awk sudo") and `limit` 1-20 (default 5). Prefer this over web_search for technique/payload/wordlist lookups — it is faster and offline.
  ```json
  {{"tool": "search_kb", "args": {{"keyword": "SQL injection bypass WAF", "limit": 5}}}}
  ```
- **suggest_exploit** — Offline exploit leads for a fingerprinted service: exact GTFOBins binary page + HackTricks + PayloadsAllTheThings hits. Run right after nmap/whatweb; follow with search_cve for exact-version CVEs.
  ```json
  {{"tool": "suggest_exploit", "args": {{"service": "apache httpd", "version": "2.4.49"}}}}
  ```
- **find_wordlist** — Find SecLists wordlists by keyword AND materialize them into medusa_agent/wordlists/ for ffuf/gobuster/hydra.
  ```json
  {{"tool": "find_wordlist", "args": {{"keyword": "directory"}}}}
  ```
- **extract_payloads** — Pull runnable code blocks from matching KB docs into medusa_agent/payloads/. Review before running.
  ```json
  {{"tool": "extract_payloads", "args": {{"keyword": "reverse shell bash", "max_payloads": 10}}}}
  ```
- **kb_stats** — Knowledge base inventory: per-source doc counts, build age, failed sources.
  ```json
  {{"tool": "kb_stats", "args": {{}}}}
  ```
"""

    catalog += """- **wordlist_tool** — Merge / dedupe / length-filter wordlists into medusa_agent/wordlists/.
  ```json
  {"tool": "wordlist_tool", "args": {"action": "merge", "files": ["wordlists/a.txt", "wordlists/b.txt"], "out": "wordlists/merged.txt", "min_len": 4}}
  ```
- **mine_failures** — Cluster the failure DB so you never repeat a blocked technique/target combo.
  ```json
  {"tool": "mine_failures", "args": {"max_clusters": 5}}
  ```
- **anonymize_report** — Scrub IPs/emails/tokens/keys from a report file into medusa_agent/reports/anonymized/ before sharing.
  ```json
  {"tool": "anonymize_report", "args": {"file_path": "reports/eng_report.md"}}
  ```
- **apply_patch** — Patch vulnerabilities in the target lab application.
  ```json
  {"tool": "apply_patch", "args": {"vulnerability": "sqli"}}
  ```
- **claim_flag** — Signal objective complete.
  ```json
  {"tool": "claim_flag", "args": {"flag": "flag{...}"}}
  ```
- **recon_chain** — One-call recon: nmap scan + service fingerprint + version-based CVE lookup.
  ```json
  {"tool": "recon_chain", "args": {"target": "TARGET"}}
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

    # Module tools — only advertise the ones whose binaries are present, and
    # list the missing ones separately with install hints so the agent never
    # wastes a turn calling a tool that cannot run.
    from medusa.tools.availability import install_hint, missing_binaries

    unavailable = missing_binaries()
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
                    if t_name in unavailable:
                        continue
                    desc = t_info.get("description", "")
                    params = t_info.get("parameters", {})
                    param_example = ", ".join(f'"{p}": "..."' for p in params)
                    catalog += f"- **{t_name}** — {desc}\n"
                    catalog += "  ```json\n"
                    if param_example:
                        catalog += f'  {{"tool": "{t_name}", "args": {{{param_example}}}}}\n'
                    else:
                        catalog += f'  {{"tool": "{t_name}", "args": {{}}}}\n'
                    catalog += "  ```\n"

    if unavailable:
        catalog += "## NOT INSTALLED — install these to unlock more tools\n"
        for t_name, missing in sorted(unavailable.items()):
            hints = "; ".join(install_hint(b) for b in missing)
            catalog += f"- **{t_name}** — missing: {', '.join(missing)}. {hints}\n"

    if _kb is None:
        catalog += (
            "## DISABLED — knowledge base not built\n"
            "- **search_kb** — offline security KB (HackTricks, PayloadsAllTheThings, GTFOBins, "
            "LOLBAS, OWASP, SecLists). The operator enables it with `medusa pull kb`. "
            "Use web_search until then.\n"
        )

    # Strategy reminder
    catalog += """
## Attack Strategy (MUST FOLLOW)
1. **Recon first** — Always start with `execute_terminal` running gobuster/nmap/nikto before manual testing. Never start with raw curl.
2. **Knowledge base before attacking** — `search_kb` BEFORE every new attack technique, payload class, privesc path, or wordlist choice. It contains HackTricks, PayloadsAllTheThings, GTFOBins, LOLBAS, OWASP and SecLists — offline, instant, richer than web_search. If it says the KB is not built, tell the operator to run `medusa pull kb`.
3. **CVE before exploit** — `search_cve` after fingerprinting a service. Don't guess.
4. **Knowledge graph before payload** — `check_knowledge` before every new payload.
5. **Verify before claiming** — Confirm exploits with tool-call evidence. No hallucinations.
6. **Log everything** — `write_note` after every significant finding.
7. **Module tools** — Use loaded module tools (above) when applicable instead of reinventing.
"""
    return catalog
