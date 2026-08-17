"""
Medusa Tool Registry — single source of truth for tool metadata.

Every tool has:
  - purpose: one-line summary
  - when_to_use: concrete scenarios, NOT abstract descriptions
  - args_format: exact JSON args the LLM must emit
  - description: full usage notes including pitfalls

This registry feeds the dynamic system prompt builder. Dict insertion
order defines tool priority (first = highest).
"""

TOOL_REGISTRY = {
    # ===== Core Execution =====
    "execute_terminal": {
        "purpose": "Run any shell command on the attack host",
        "when_to_use": (
            "Use for CLI pentest tools: nmap, gobuster, ffuf, sqlmap, hydra, "
            "john, nikto, amass, nuclei, katana, httpx, feroxbuster, searchsploit. "
            "Also for Python scripts, file operations, and any command-line work. "
            "Prefer dedicated CLI tools over raw http_request for scanning/brute-forcing."
        ),
        "args_format": '"cmd": "full shell command here"',
        "long_running": True,
        "description": (
            "**execute_terminal** — runs commands on the attack host.\n"
            "- Commands run in the medusa_agent/ workspace directory.\n"
            "- Timeout: 30s default. Long scans (nmap -p-, gobuster with big wordlists) will be truncated.\n"
            "- Dangerous commands (pip install, sudo, rm -rf /) trigger user confirmation.\n"
            "- DO NOT chain with && excessively — keep commands focused.\n"
            "- Output is truncated at 8000 chars. Use write_file to save full output if needed.\n"
            "- ⚠️ LONG-RUNNING — for scans/brute-force, set '\"background\": true' in tool_args to run async."
        ),
    },

    # ===== HTTP =====
    "http_request": {
        "purpose": "Send a raw HTTP request with browser-emulation headers",
        "when_to_use": (
            "Manual payload testing against a specific endpoint. Use when you need "
            "to send a crafted request and inspect the exact response. NOT for scanning "
            "(use execute_terminal with gobuster/nmap instead). Good for: testing a single "
            "SQLi payload, checking a specific URL, verifying auth bypass, reading robots.txt."
        ),
        "args_format": '"method": "GET|POST|PUT|DELETE", "url": "http://target/path", "headers": {"Header": "value"}, "body": "POST data"',
        "description": (
            "**http_request** — raw HTTP with Chrome macOS emulation.\n"
            "- Automatically adds User-Agent, Accept, Sec-Ch-Ua headers.\n"
            "- Follows redirects. verify=False (accepts self-signed certs).\n"
            "- Returns: Status, Headers, Cookies, Body.\n"
            "- Session cookies persist across calls (requests.Session)."
        ),
    },

    # ===== File Operations =====
    "read_file": {
        "purpose": "Read any file from disk",
        "when_to_use": "Read wordlists, previous tool output, config files, scripts you wrote earlier, /etc/passwd, source code.",
        "args_format": '"file_path": "path/to/file.txt"',
        "description": "**read_file** — reads file content. Relative paths resolve from medusa_agent/. Absolute paths also work.",
    },
    "write_file": {
        "purpose": "Write content to a file",
        "when_to_use": "Save scripts, payloads, wordlists, notes, HTML responses for analysis, exploit code.",
        "args_format": '"file_path": "scripts/exploit.py", "content": "#!/usr/bin/env python3\\n..."',
        "description": "**write_file** — creates/writes file. Relative paths go to medusa_agent/. Creates parent dirs automatically.",
    },

    # ===== Knowledge & Intelligence =====
    "search_kb": {
        "purpose": "Full-text search the local security knowledge base (HackTricks, PayloadsAllTheThings, GTFOBins, LOLBAS, OWASP cheat sheets, SecLists)",
        "when_to_use": "Look up exploitation techniques, payload syntax, living-off-the-land binaries, wordlist names, cheat sheets BEFORE attempting an attack — this is faster and richer than web_search.",
        "args_format": '"keyword": "source:gtfobins awk sudo", "limit": 5',
        "description": "**search_kb** — BM25-ranked search over the local knowledge base (kb.sqlite3). Returns ranked matches with source and snippet. Optional `source:<name>` filter scopes to one KB source; `limit` 1-20 (default 5). If not built, asks the operator to run `medusa pull kb`.",
    },
    "search_cve": {
        "purpose": "Query NVD for CVEs by software name and version",
        "when_to_use": (
            "After fingerprinting a service with nmap or whatweb. Always search CVEs BEFORE "
            "attempting exploitation — don't guess. Example: found Apache 2.4.49 → search_cve "
            "for path traversal CVE-2021-41773."
        ),
        "args_format": '"software": "apache httpd", "version": "2.4.49", "limit": 5',
        "description": "**search_cve** — queries NVD API. Requires nvd_api_key in config. Returns CVE IDs, descriptions, CVSS scores.",
    },
    "check_knowledge": {
        "purpose": "Query the knowledge graph before generating payloads",
        "when_to_use": (
            "ALWAYS call before crafting a new payload. Checks if the payload pattern "
            "is already known to be blocked/WAF'd. Saves wasted attempts."
        ),
        "args_format": '"target": "TARGET_HOST", "payload": "optional specific payload to check"',
        "description": "**check_knowledge** — queries knowledge_graph.json for known blocked patterns, WAF rules, and verified CVEs.",
    },
    "record_finding": {
        "purpose": "Persist a verified finding to the knowledge graph",
        "when_to_use": "After you CONFIRM a constraint (WAF blocks X, CVE Y works, parameter Z is injectable). Log EVERY verified fact.",
        "args_format": '"target": "TARGET", "finding_type": "blocks|verified_cve|bypass|behavior", "rule": "the rule", "evidence": "what proved it"',
        "description": "**record_finding** — writes to knowledge_graph.json. Deduplicates automatically. Confidence 1.0 for binary-verified findings.",
    },
    "write_note": {
        "purpose": "MANDATORY — Log EVERY action, finding, and decision to the engagement file",
        "when_to_use": "After EVERY tool call without exception. Tested endpoint? write_note. Found a vulnerability? write_note. Tool failed? write_note. This is NOT optional — your engagement report depends on these notes.",
        "args_format": '"content": "What happened in detail...", "success": true, "category": "recon|exploit|cve|blocked|finding|progress|complete", "engagement": "target-name"',
        "description": "**write_note** — MANDATORY after every action. Categories: recon, exploit, cve, blocked, oracle, finding, progress, complete. Notes build your final report. SKIP AT YOUR PERIL — the audit trail and final report depend on these.",
    },
    "deploy_subagent": {
        "purpose": "Spawn parallel subagents to attack different targets/vectors simultaneously",
        "when_to_use": "Multiple ports discovered? Spawn one subagent per port. Multiple vuln types to test? One subagent each. Long scan running? Spawn subagent for active probing. Use || to separate multiple tasks: 'task1 || task2 || task3'.",
        "args_format": 'Use action="deploy_subagent" with subagent_task field (not tool_name/tool_args). Tasks separated by || for parallel execution.',
        "description": "**deploy_subagent** — ACTION type (not a tool call). Use action=\"deploy_subagent\" with subagent_task=\"task description\". Subagents have full tool access, run independently, and return results. Max 3 concurrent. AGGRESSIVELY deploy subagents — they 10x your throughput.",
    },

    # ===== Metasploit =====
    "msf_check": {
        "purpose": "Verify Metasploit RPC connectivity",
        "when_to_use": "Once at the start of exploitation phase to confirm MSF is available.",
        "args_format": '(no args)',
        "description": "**msf_check** — tests Metasploit RPC connection. Returns version info.",
    },
    "msf_command": {
        "purpose": "Run raw msfconsole commands",
        "when_to_use": "Search for modules, show options, set globals. NOT for running exploits (use msf_run).",
        "args_format": '"cmd": "search eternalblue"',
        "description": "**msf_command** — raw msfconsole via RPC. For search, info, version commands.",
    },
    "msf_run": {
        "purpose": "Execute a Metasploit module (exploit/auxiliary/post)",
        "when_to_use": "Run an exploit against a confirmed CVE. Set up a handler for reverse shells. Run post-exploitation modules.",
        "args_format": '"module": "exploit/multi/handler", "payload": "windows/meterpreter/reverse_tcp", "options": {"LHOST": "10.0.0.5", "LPORT": "4444"}}',
        "description": "**msf_run** — executes MSF module via RPC. Returns session info on success.",
    },
    "msf_sessions": {
        "purpose": "Manage Meterpreter sessions",
        "when_to_use": "List active sessions, interact with a session, or kill a dead session.",
        "args_format": '"action": "list|info|kill", "id": 1',
        "description": "**msf_sessions** — session management. action=list to see all, action=info for details.",
    },

    # ===== Special =====
    "apply_patch": {
        "purpose": "Apply a security patch to the target lab application",
        "when_to_use": "After successfully exploiting a vulnerability, patch it and try the next one. Used in benchmark mode.",
        "args_format": '"vulnerability": "sqli|command_injection|ssrf|lfi", "file_path": "lab.py"',
        "description": "**apply_patch** — patches lab.py. Supported: sqli, command_injection, ssrf, lfi.",
    },
    "claim_flag": {
        "purpose": "Signal that the objective is complete",
        "when_to_use": "ONLY when you have verified proof of objective completion. Do NOT claim prematurely.",
        "args_format": '"flag": "flag{...}"',
        "description": "**claim_flag** — ends the engagement. Only use with confirmed flag/evidence.",
    },

    # ===== Creative Freedom =====
    "web_search": {
        "purpose": "Search the internet for exploit techniques, documentation, CVE details",
        "when_to_use": "Research CVEs, find exploit PoCs, look up documentation, learn new techniques.",
        "args_format": '"query": "search terms here", "max_results": 5',
        "description": "**web_search** — searches DuckDuckGo. Returns titles, snippets, URLs. Use for research.",
    },
    "pip_install": {
        "purpose": "Install Python packages the agent needs",
        "when_to_use": "Install tools like pwntools, impacket, paramiko for exploitation.",
        "args_format": '"package": "package-name"',
        "description": "**pip_install** — installs Python packages via pip. Confirmation required for safety.",
    },
    "edit_skill": {
        "purpose": "Improve your own hacking methodology by editing skill prompts",
        "when_to_use": "When you discover a better technique, codify it into the appropriate skill file.",
        "args_format": '"skill_name": "sql_injection", "new_content": "improved workflow"',
        "description": "**edit_skill** — overwrites an attack skill. Self-improvement: learn→codify→improve.",
    },
    "write_tool": {
        "purpose": "Create new Python tools to extend your capabilities",
        "when_to_use": "When existing tools are insufficient, write a custom tool.",
        "args_format": '"tool_name": "my_scanner", "code": "def run(): ..."',
        "description": "**write_tool** — creates a new Python tool in medusa/tools/. Auto-loaded on next run.",
    },
    "list_skills": {
        "purpose": "See all attack skills you can edit and improve",
        "when_to_use": "Before editing a skill, check what's available.",
        "args_format": "(no args)",
        "description": "**list_skills** — lists all editable skill files in medusa/skills/.",
    },
    "list_own_files": {
        "purpose": "See all code files you can read and modify",
        "when_to_use": "Explore your own codebase to understand and improve yourself.",
        "args_format": "(no args)",
        "description": "**list_own_files** — lists all Python files the agent can read/modify.",
    },

    # ===== Background Jobs =====
    "job_status": {
        "purpose": "Check status of a background job",
        "when_to_use": "After spawning nmap/gobuster/sqlmap — check if scan is done.",
        "args_format": '"job_id": "abc123"',
        "description": "**job_status** — returns job status, elapsed time, and preview of output.",
    },
    "job_wait": {
        "purpose": "Wait for a background job to complete",
        "when_to_use": "When you need results before proceeding. Blocks up to timeout seconds.",
        "args_format": '"job_id": "abc123", "timeout": 60',
        "description": "**job_wait** — polls job every second until done or timeout. Returns final status.",
    },
    "job_output": {
        "purpose": "Get full output from a completed background job",
        "when_to_use": "After a job completes — read the full scan/bruteforce output.",
        "args_format": '"job_id": "abc123"',
        "description": "**job_output** — returns full output (up to 4000 chars) from a completed job.",
    },
    "job_list": {
        "purpose": "List all running and completed background jobs",
        "when_to_use": "See what scans are running, what's done, what failed.",
        "args_format": "(no args)",
        "description": "**job_list** — lists all background jobs with status and elapsed time.",
    },
    "job_cancel": {
        "purpose": "Cancel a running background job",
        "when_to_use": "Stop a scan that's taking too long or going nowhere.",
        "args_format": '"job_id": "abc123"',
        "description": "**job_cancel** — marks a job as cancelled. Thread continues but result is ignored.",
    },

    # ===== Analysis & Reporting =====
    "payload_generate": {
        "purpose": "Generate context-aware attack payloads for a vulnerability type",
        "when_to_use": "Before testing SQLi, XSS, SSTI, SSRF, LFI, JWT, or command injection. Get the right payloads for the target framework.",
        "args_format": '"vuln_type": "sqli|xss|ssti|ssrf|lfi|jwt|command_injection", "framework": "mysql|jinja2|..."',
        "description": "**payload_generate** — returns tested payloads. Omit framework to list all available types.",
    },
    "diff_response": {
        "purpose": "Compare two HTTP responses to detect anomalies from parameter injection",
        "when_to_use": "After injecting a payload into a parameter. Compare baseline response vs injected response to detect SQL errors, stack traces, length changes, status code changes.",
        "args_format": '"baseline": "normal response body", "injected": "response after injection", "sensitivity": "low|medium|high"',
        "description": "**diff_response** — auto-detects: status changes, error disclosure, length anomalies, new sensitive data. Returns JSON with anomaly list.",
    },
    "rate_limit_check": {
        "purpose": "Check if an endpoint is being rate-limited",
        "when_to_use": "When you get 429 responses or suspect rate limiting. Returns rate limit status and recommended wait time.",
        "args_format": '"endpoint": "http://target.com/api/login"',
        "description": "**rate_limit_check** — tracks per-endpoint request counts and latencies. Auto-recommends backoff.",
    },
    "rate_limit_all": {
        "purpose": "Get rate limit status for all tracked endpoints",
        "when_to_use": "During heavy scanning to see which endpoints are blocked.",
        "args_format": "(no args)",
        "description": "**rate_limit_all** — lists all endpoints with request counts, avg latency, and rate limit status.",
    },
    "attack_tree": {
        "purpose": "Generate a Mermaid flowchart of attack paths from execution trace",
        "when_to_use": "When writing a report or analysing attack chains. Visualizes the path from recon to flag.",
        "args_format": '"trace_json": "[{\"tool_name\":\"nmap_scan\",\"success\":true,...}]"',
        "description": "**attack_tree** — creates Mermaid graph markup. Paste into any Mermaid renderer.",
    },
    "generate_report": {
        "purpose": "Generate a comprehensive Markdown engagement report",
        "when_to_use": "At the END of every engagement. ALWAYS call this before completing. Saves to medusa_agent/reports/.",
        "args_format": '"engagement": "target name", "trace_json": "[...]", "findings_json": "[...]"',
        "description": "**generate_report** — creates detailed Markdown report with finding tables, attack chains, Mermaid diagrams, and execution trace. ALWAYS use before calling complete.",
    },
}

# ── FREEDOM: all tools available in all phases ──────────────────────
_ALL_TOOLS = {
    "execute_terminal", "http_request", "read_file", "write_file",
    "search_kb", "search_cve", "check_knowledge", "record_finding",
    "write_note",
    "msf_check", "msf_command", "msf_run", "msf_sessions",
    "apply_patch", "claim_flag",
    "web_search", "list_skills", "list_own_files", "pip_install",
    "edit_skill", "write_tool",
    "job_status", "job_wait", "job_output", "job_list", "job_cancel",
    "payload_generate", "diff_response", "rate_limit_check", "rate_limit_all",
    "attack_tree", "generate_report",
}

PHASE_TOOLS = {
    "informational": _ALL_TOOLS,
    "exploitation": _ALL_TOOLS,
    "post_exploitation": _ALL_TOOLS,
}


def is_tool_allowed_in_phase(tool_name: str, phase: str) -> bool:
    """Check if a tool is allowed in the given phase."""
    allowed = PHASE_TOOLS.get(phase, set())
    return tool_name in allowed


def get_allowed_tools_for_phase(phase: str) -> list[str]:
    """Return list of tool names allowed in the given phase."""
    return sorted(PHASE_TOOLS.get(phase, set()))


def build_tool_catalog_prompt(phase: str = "informational") -> str:
    """Build a dynamic tool catalog section for the system prompt.

    Only includes tools allowed in the current phase. Includes purpose,
    when_to_use, and args_format for each tool.
    """
    allowed = get_allowed_tools_for_phase(phase)
    lines = ["## Available Tools (phase: {})".format(phase), ""]

    for tool_name in allowed:
        info = TOOL_REGISTRY.get(tool_name)
        if not info:
            continue
        lines.append(f"### {tool_name}")
        if info.get("long_running"):
            lines.append("⚠️  **LONG-RUNNING TOOL** — ALWAYS use `\"background\": true` in args!")
        lines.append(f"**Purpose**: {info['purpose']}")
        lines.append(f"**When to use**: {info['when_to_use']}")
        lines.append(f"**Args**: `{info['args_format']}`")
        lines.append("")

    lines.append("## ⚠️ BACKGROUND EXECUTION (CRITICAL)")
    lines.append("Some tools take 30s–10min to complete. You MUST run them as background jobs")
    lines.append("to avoid blocking the agent loop. Set `\"background\": true` in tool_args.")
    lines.append("")
    lines.append("**Tools that ALWAYS need background**:")
    lines.append("- `nmap_scan` with -p- or --script flags → `{\"target\": \"...\", \"flags\": \"-sV -sC -p-\", \"background\": true}`")
    lines.append("- `gobuster_dir` / `gobuster_dns` with large wordlists")
    lines.append("- `ffuf_fuzz` with large wordlists")
    lines.append("- `feroxbuster_scan` with large wordlists")
    lines.append("- `nikto_scan` (always long)")
    lines.append("- `sqlmap_scan` (always long)")
    lines.append("- `hydra_brute` with large wordlists")
    lines.append("- `amass_enum` with active enumeration")
    lines.append("- `execute_terminal` with scan/brute-force commands")
    lines.append("")
    lines.append("**Background workflow**:")
    lines.append("1. Spawn: `{\"tool_name\": \"nmap_scan\", \"tool_args\": {\"target\": \"X\", \"background\": true}}`")
    lines.append("2. You get a job_id back immediately. Continue other work.")
    lines.append("3. Check: `job_status {\"job_id\": \"abc123\"}` or `job_list`")
    lines.append("4. Collect: `job_wait {\"job_id\": \"abc123\", \"timeout\": 300}` then `job_output`")
    lines.append("")

    lines.append("## Attack Strategy (MUST FOLLOW)")
    lines.append("1. **Recon first** — nmap/gobuster/fingerprint BEFORE touching any parameter.")
    lines.append("2. **CVE before exploit** — search_cve after fingerprinting. Don't guess.")
    lines.append("3. **KG before payload** — check_knowledge before every new payload.")
    lines.append("4. **Verify before claiming** — confirm exploits with evidence. No hallucinations.")
    lines.append("5. **Log everything** — write_note after every finding, even negatives.")
    lines.append("6. **One tool per turn** — emit exactly ONE tool call per decision.")

    return "\n".join(lines)
