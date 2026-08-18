"""
suijin/fugu.py — Pragmatic Collective Intelligence Orchestrator

Decomposes a high-level objective into a phased task graph, then runs each phase
sequentially with role-gated agents. Shared state lives in the Knowledge Graph.
No async message buses, no 8-process pools — just smart task decomposition and
sequential phase execution.

Architecture:
  FuguPlanner:  LLM decomposes objective → JSON task graph
  TaskGraph:    DAG of phases with dependencies, retry, fallback
  FuguAgent:    Runs a single phase with role-gated tools, writes to KG
"""

import json
import re
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

console = Console()
BASE_DIR = Path(__file__).resolve().parent

# ---- Agent Roles -----------------------------------------------------------
ROLES = {
    "recon": {
        "name": "Reconnaissance",
        "description": "Port scanning, directory bruteforce, subdomain enum, fingerprinting, CVE lookup.",
        "tools": {
            "nmap_scan", "gobuster_dir", "gobuster_dns", "feroxbuster_scan",
            "amass_enum", "search_cve", "http_request", "curl_request",
            "sslscan_check", "execute_terminal", "read_file", "write_file",
            "search_kb", "write_note", "check_knowledge", "record_finding",
        },
        "prompt_prefix": "You are a RECONNAISSANCE specialist. Map the target thoroughly. "
                         "Use scanning tools, fingerprint services, search CVEs. "
                         "Do NOT attempt exploitation — that is handled by a later phase. "
                         "Write every finding to the Knowledge Graph.",
    },
    "exploit": {
        "name": "Exploitation",
        "description": "SQL injection, XSS, LFI, Metasploit exploitation of known CVEs.",
        "tools": {
            "sqlmap_scan", "msf_run", "msf_command", "msf_check", "msf_sessions",
            "hydra_brute", "http_request", "curl_request", "execute_terminal",
            "write_file", "read_file", "write_note", "check_knowledge", "record_finding",
        },
        "prompt_prefix": "You are an EXPLOITATION specialist. Reconnaissance has completed — "
                         "findings are in the Knowledge Graph. Directly exploit discovered "
                         "vulnerabilities using the recon data. Do NOT re-scan or re-enumerate.",
    },
    "escalate": {
        "name": "Privilege Escalation",
        "description": "Local privilege escalation, SUID/SGID abuse, kernel exploits, token manipulation.",
        "tools": {
            "execute_terminal", "msf_run", "msf_command", "msf_sessions",
            "write_file", "read_file", "http_request", "curl_request",
            "write_note", "check_knowledge", "record_finding",
        },
        "prompt_prefix": "You are a PRIVILEGE ESCALATION specialist. You have a foothold on "
                         "the target. Escalate to SYSTEM/root using local exploits, SUID binaries, "
                         "misconfigurations, or Metasploit post-exploitation modules.",
    },
    "persist": {
        "name": "Persistence",
        "description": "Registry run keys, scheduled tasks, cron jobs, SSH keys, services.",
        "tools": {
            "execute_terminal", "write_file", "read_file",
            "msf_run", "msf_command", "msf_sessions",
            "write_note", "check_knowledge", "record_finding",
        },
        "prompt_prefix": "You are a PERSISTENCE specialist. Establish a long-term presence on "
                         "the target so access is maintained across reboots. Use scheduled tasks, "
                         "services, SSH keys, or Metasploit persistence modules.",
    },
    "lateral": {
        "name": "Lateral Movement",
        "description": "PsExec, WMI, WinRM, SSH, Pass-the-Hash, SMB relay.",
        "tools": {
            "execute_terminal", "msf_run", "msf_command", "msf_sessions",
            "hydra_brute", "socat_relay", "http_request", "curl_request",
            "write_file", "read_file", "write_note", "check_knowledge", "record_finding",
        },
        "prompt_prefix": "You are a LATERAL MOVEMENT specialist. Move from the compromised "
                         "host to other targets on the network using Pass-the-Hash, PsExec, "
                         "WinRM, or SSH lateral movement techniques.",
    },
    "report": {
        "name": "Reporting",
        "description": "Compile findings, generate engagement reports.",
        "tools": {
            "write_file", "read_file", "execute_terminal",
            "write_note", "check_knowledge", "claim_flag",
        },
        "prompt_prefix": "You are a REPORTING specialist. Compile all findings from previous "
                         "phases into a structured report. Summarize attack chain, list CVEs, "
                         "document exploited vulnerabilities, and claim the flag.",
    },
}


# ---- Task Graph ------------------------------------------------------------
class TaskGraph:
    """A directed acyclic graph of phases. Each phase runs after dependencies complete."""

    def __init__(self):
        self.phases = []        # list of phase dicts in execution order

    @classmethod
    def from_json(cls, data):
        """Parse a JSON task graph from the LLM planner."""
        tg = cls()
        for p in data.get("phases", []):
            tg.phases.append({
                "id": p.get("id", f"phase-{len(tg.phases)+1}"),
                "role": p.get("role", "recon"),
                "objective": p.get("objective", ""),
                "depends_on": p.get("depends_on", []),
                "tools": p.get("tools", []),
                "success_criteria": p.get("success_criteria", ""),
                "max_turns": p.get("max_turns", 10),
                "status": "pending",
            })
        return tg

    def ready_phases(self):
        """Return phases whose dependencies are satisfied and status is pending.

        A dependency is satisfied if it is 'complete' or 'exhausted'.
        If any dependency 'failed', the phase is blocked — mark it 'blocked'.
        """
        satisfied_ids = {p["id"] for p in self.phases if p["status"] in ("complete", "exhausted")}
        failed_ids = {p["id"] for p in self.phases if p["status"] == "failed"}
        ready = []
        for p in self.phases:
            if p["status"] != "pending":
                continue
            deps = p.get("depends_on", [])
            # If any dependency failed, block this phase
            if any(dep in failed_ids for dep in deps):
                p["status"] = "blocked"
                continue
            deps_met = all(dep in satisfied_ids for dep in deps)
            if deps_met:
                ready.append(p)
        return ready

    def mark(self, phase_id, status):
        for p in self.phases:
            if p["id"] == phase_id:
                p["status"] = status
                return

    def summary(self):
        lines = []
        for p in self.phases:
            icon = {"pending":"⏳","in_progress":"🔄","complete":"✅","failed":"❌","exhausted":"⏹️","blocked":"🚫"}.get(p["status"],"?")
            lines.append(f"  {icon} {p['id']} [{p['role']}] — {p['objective'][:60]}")
        return "\n".join(lines)


# ---- Fugu Planner ----------------------------------------------------------
PLANNER_PROMPT = """# ROLE: Suijin Mission Planner
You decompose a high-level red-team objective into a phased task graph.

# OUTPUT: EXACTLY ONE JSON OBJECT with this schema:
{
  "phases": [
    {
      "id": "phase-1",
      "role": "recon|exploit|escalate|persist|lateral|report",
      "objective": "Specific, concrete objective for this phase only",
      "depends_on": [],
      "tools": ["tool_name_1", "tool_name_2"],
      "success_criteria": "How to know this phase is complete",
      "max_turns": 10
    }
  ]
}

# AVAILABLE ROLES:
- recon: port scanning, directory bruteforce, subdomain enum, fingerprinting, CVE search
- exploit: SQL injection, XSS, Metasploit exploitation, credential brute-force
- escalate: privilege escalation (SUID, kernel exploits, sudo abuse)
- persist: establish long-term access (scheduled tasks, services, SSH keys)
- lateral: move to other targets on the network (PsExec, WinRM, SSH, Pass-the-Hash)
- report: compile findings into structured output, claim flag

# PLANNING RULES:
1. Always start with recon — you cannot exploit what you haven't discovered
2. Each phase depends on previous phases (recon → exploit → escalate → persist/report)
3. Make objectives concrete and specific: "Scan ports 1-1000 on 10.0.0.5" not "do recon"
4. Limit to 3-5 phases for most objectives
5. Include a report phase at the end to claim the flag
6. List only tools that exist (nmap_scan, gobuster_dir, sqlmap_scan, hydra_brute, msf_run, msf_command, msf_sessions, search_cve, execute_terminal, http_request, curl_request, write_file, read_file, write_note, check_knowledge, record_finding, claim_flag, sslscan_check, amass_enum, nikto_scan, john_crack, feroxbuster_scan, ffuf_fuzz, socat_relay, wifi_scan, wifi_capture, wifi_crack, apply_patch, search_kb)

Generate the task graph NOW. Only the JSON. No other text.
"""


def _call_planner(generate_fn, config, objective):
    """Call the LLM to decompose the objective into a task graph."""
    messages = [
        {"role": "system", "content": PLANNER_PROMPT},
        {"role": "user", "content": f"OBJECTIVE: {objective}"},
    ]
    try:
        resp = generate_fn(messages, config, temperature=0.2, max_tokens=800)
    except Exception as e:
        console.print(f"[red]Planner LLM failed: {e}[/red]")
        return None

    if isinstance(resp, str) and resp.startswith("Error:"):
        console.print(f"[red]Planner returned error: {resp}[/red]")
        return None

    # Extract JSON
    try:
        m = re.search(r"\{[\s\S]*\}", resp)
        raw = m.group(0) if m else resp
        raw = re.sub(r",\s*\}", "}", raw)
        data = json.loads(raw)
        return data
    except Exception:
        console.print(f"[yellow]Planner JSON parse failed. Raw: {resp[:200]}[/yellow]")
        return None


# ---- Fugu Agent Loop -------------------------------------------------------
def _build_agent_prompt(role_def, phase, task_graph_summary):
    """Build the system prompt for a single phase agent."""
    return (
        f"{role_def['prompt_prefix']}\n\n"
        f"# CURRENT PHASE\n"
        f"Phase: {phase['id']} [{role_def['name']}]\n"
        f"Objective: {phase['objective']}\n"
        f"Success: {phase.get('success_criteria', 'Complete the objective')}\n"
        f"Max turns: {phase.get('max_turns', 10)}\n"
        f"\n# TASK GRAPH STATUS\n{task_graph_summary}\n"
        f"\n# AVAILABLE TOOLS (only these):\n"
        + "".join(f"- {t}\n" for t in sorted(role_def["tools"]))
        + "\n# RULES\n"
        "1. You are ONLY authorized to use the tools listed above.\n"
        "2. Consult the Knowledge Graph (check_knowledge) for findings from previous phases.\n"
        "3. Record ALL findings with record_finding and write_note.\n"
        "4. When the success criteria are met, write a final note and wait for the next phase.\n"
        "5. End each response with exactly ONE JSON tool block.\n"
    )


def _role_gated_route(tool_name, args, role_def, config):
    """Route a tool call but block unauthorized tools for this role."""
    from suijin import tools
    if tool_name not in role_def["tools"]:
        return (
            f"⛔ TOOL GATED — '{tool_name}' is not authorized for the "
            f"{role_def['name']} role.\n"
            f"Authorized tools: {', '.join(sorted(role_def['tools']))}\n"
            f"Use only the tools assigned to your role."
        )
    return str(tools.route_tool(tool_name, args, config))


def _extract_target_from_objective(objective):
    """Pull a hostname or IP from the objective string for scoping chain analysis."""
    # URL first (most specific)
    m = re.search(r'https?://([^\s/]+)', objective)
    if m:
        return m.group(1).rstrip('/')
    # Bare domain with TLD
    m = re.search(r'\b([a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,})\b', objective)
    if m:
        return m.group(1)
    # IPv4
    m = re.search(r'\b(\d{1,3}(?:\.\d{1,3}){3})\b', objective)
    if m:
        return m.group(1)
    return None


# ---- Adapters to current architecture (replaces legacy redteamer imports) ----

def _ai_call(messages, config):
    """LLM call via the shared providers module (was: redteamer.ai_call)."""
    from suijin.modules.loader import load_local_module
    return load_local_module("providers").generate(messages, config)


def _extract_tool(resp):
    """Parse a legacy-format tool block {"tool", "args"} from an LLM response.

    Tolerates the modern {"action","tool_name","tool_args"} shape too.
    """
    if not isinstance(resp, str):
        return None
    from suijin.helpers.parsing import try_parse_llm_decision
    decision, err = try_parse_llm_decision(resp)
    if decision and decision.get("action") in ("use_tool", "plan_tools"):
        return {"tool": decision.get("tool_name", "unknown"),
                "args": decision.get("tool_args", {})}
    # Fallback: bare {"tool": ..., "args": ...} blocks (balanced-brace scan)
    plain = re.sub(r'```(?:json)?', '', resp).strip()
    start = plain.find('{')
    if start != -1:
        depth = 0
        for i in range(start, len(plain)):
            if plain[i] == '{':
                depth += 1
            elif plain[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(plain[start:i+1])
                        if isinstance(data, dict) and "tool" in data:
                            return data
                    except Exception:
                        pass
                    break
    return None


def _action_trail_context():
    """Recent audit-trail summary for prompt context (was: get_action_trail_context)."""
    try:
        from suijin.tools.audit_trail import get_audit_json
        trail = get_audit_json()
        iters = trail.get("iterations", [])[-5:] if trail else []
        if not iters:
            return ""
        lines = ["\n\n# RECENT ACTION TRAIL:"]
        for it in iters:
            tn = it.get("tool_name", "?")
            ok = "✓" if it.get("success", True) else "✗"
            out = str(it.get("tool_output", ""))[:120]
            lines.append(f"- {ok} {tn}: {out}")
        return "\n".join(lines)
    except Exception:
        return ""


def _tutorial_knowledge():
    """Tutorial knowledge snippet (was: get_tutorial_knowledge)."""
    try:
        tutorials = Path(__file__).parent / "tutorials"
        if not tutorials.exists():
            return ""
        core = tutorials / "Core"
        files = sorted(core.glob("*.md"))[:2] if core.exists() else []
        if not files:
            return ""
        parts = ["\n\n# TUTORIAL KNOWLEDGE:"]
        for f in files:
            parts.append(f.read_text(encoding="utf-8", errors="ignore")[:1500])
        return "\n".join(parts)
    except Exception:
        return ""


def _host_os_directive():
    """Host OS directive for tool syntax (was: get_host_os_directive)."""
    import platform
    system = platform.system()
    if system == "Darwin":
        return ("\n\n# HOST OS DIRECTIVE\nYou run on macOS. Use BSD tools "
                "(nmap, curl, python3). brew paths are on PATH. No apt/yum.")
    if system == "Linux":
        return ("\n\n# HOST OS DIRECTIVE\nYou run on Linux. Use GNU tools "
                "(nmap, curl, python3). Prefer python3 scripts over shell.")
    return "\n\n# HOST OS DIRECTIVE\nYou run on an unknown OS. Use portable tools."


def run_fugu(config, objective, generate_fn, api_key=None):
    """Entry point: decompose objective → build task graph → run each phase.

    Args:
        config:      app config dict
        objective:   user's high-level objective
        generate_fn: LLM generate function from providers
        api_key:     optional enterprise auth key
    """
    from suijin.fugu_chain import ChainTracker
    from suijin.intel.supervisor import format_spend
    from suijin.modules.loader import load_local_module

    providers = load_local_module("providers")
    tools = load_local_module("tools")

    console.print(Panel.fit("[bold cyan]🐡 Fugu Collective Intelligence Mode[/bold cyan]"))

    # ---- Step 0: Initialize chain tracker ----
    chainer = ChainTracker()
    target = _extract_target_from_objective(objective)

    # ---- Step 1: Plan ----
    console.print("[bold]Planning mission phases...[/bold]")
    plan_data = _call_planner(generate_fn, config, objective)

    if not plan_data:
        console.print("[yellow]Planner returned empty — using default 3-phase plan.[/yellow]")
        plan_data = {
            "phases": [
                {"id":"phase-1","role":"recon","objective":"Scan target, enumerate services, search CVEs",
                 "depends_on":[],"tools":["nmap_scan","gobuster_dir","search_cve"],"success_criteria":"At least 3 services identified and CVEs searched","max_turns":8},
                {"id":"phase-2","role":"exploit","objective":"Exploit discovered vulnerabilities",
                 "depends_on":["phase-1"],"tools":["sqlmap_scan","msf_run","hydra_brute"],"success_criteria":"At least 1 shell/session obtained or flag captured","max_turns":10},
                {"id":"phase-3","role":"report","objective":"Compile findings, capture flag",
                 "depends_on":["phase-2"],"tools":["write_note","claim_flag","write_file"],"success_criteria":"Flag captured","max_turns":5},
            ]
        }

    task_graph = TaskGraph.from_json(plan_data)
    console.print(Panel(task_graph.summary(), title="Mission Plan"))

    # ---- Step 2: Execute phases ----
    providers.reset_usage()
    tools.reset_recon_state()
    flag_captured = False

    for _iteration in range(20):  # safety cap
        ready = task_graph.ready_phases()
        if not ready:
            pending = [p for p in task_graph.phases if p["status"] == "pending"]
            blocked = [p for p in task_graph.phases if p["status"] == "blocked"]
            if pending:
                if blocked:
                    console.print(f"[yellow]⚠️  {len(blocked)} phase(s) blocked by failed dependencies. Continuing with remaining phases.[/yellow]")
                console.print(f"[dim]Waiting for dependencies: {[p['depends_on'] for p in pending]}[/dim]")
                time.sleep(1)
                continue
            else:
                console.print("[green]All phases resolved![/green]")
                break

        for phase in ready:
            role_def = ROLES.get(phase["role"], ROLES["recon"])
            task_graph.mark(phase["id"], "in_progress")

            console.print(f"\n[bold cyan]{'─'*50}[/bold cyan]")
            console.print(f"[bold]🐡 Phase: {phase['id']} — {role_def['name']}[/bold]")
            console.print(f"[dim]Objective: {phase['objective']}[/dim]")

            # Build agent prompt with chain context from previous phases
            agent_prompt = _build_agent_prompt(role_def, phase, task_graph.summary())
            chain_context = chainer.analyze(phase["role"], phase["objective"], target=target)
            if chain_context:
                agent_prompt += chain_context
            agent_prompt += f"\n\n# RECENT ACTION TRAIL:\n{_action_trail_context()}\n"
            agent_prompt += _tutorial_knowledge()
            agent_prompt += _host_os_directive()

            messages = [
                {"role": "system", "content": agent_prompt},
                {"role": "user", "content": f"PHASE OBJECTIVE: {phase['objective']}"},
            ]

            # Run agent loop for this phase
            max_turns = phase.get("max_turns", 10)
            phase_success = False
            for turn in range(1, max_turns + 1):
                console.print(f"\n[bold]  🐡 Phase {phase['id']} · Turn {turn}/{max_turns}[/bold]")

                try:
                    resp = _ai_call(messages, config)
                except Exception as e:
                    console.print(f"[red]LLM call failed: {e}[/red]")
                    break

                if isinstance(resp, str) and resp.startswith("Error:"):
                    console.print(f"[red]{resp}[/red]")
                    break

                messages.append({"role": "assistant", "content": resp})

                plain = re.sub(r'```(?:json)?.*?```', '', resp, flags=re.DOTALL).strip()
                if plain:
                    console.print(Panel(plain[:500], title=f"{role_def['name']} Log", border_style="blue"))

                tool = _extract_tool(resp)
                if tool:
                    t_name = tool.get("tool", "unknown")
                    t_args = tool.get("args", {})
                    console.print(f"[dim]  → {t_name}: {str(t_args)[:80]}...[/dim]")

                    res = _role_gated_route(t_name, t_args, role_def, config)
                    console.print(Panel(res[:1500], title="Result", border_style="green"))

                    if t_name == "claim_flag":
                        console.print(f"[bold green]🏁 FLAG CAPTURED: {t_args.get('flag', '?')}[/bold green]")
                        flag_captured = True
                        phase_success = True

                    messages.append({"role": "user", "content": f"Result: {res}"})

                    # Oracle check on anomalies
                    if t_name in ("http_request", "execute_terminal") and res and "Error" not in res[:50]:
                        try:
                            oracle_mod = load_local_module("oracle")
                            status_match = re.search(r"Status:\s*(\d{3})", res)
                            hs = int(status_match.group(1)) if status_match else None
                            anom = oracle_mod.detect_anomaly(res, status_code=hs)
                            if anom.get("anomaly"):
                                console.print("[yellow]  [Oracle] Anomaly — diagnosing...[/yellow]")
                                verdict, added = oracle_mod.diagnose(
                                    res, hs, str(t_args)[:200], str(t_args.get("url", "")), config,
                                    lambda m,u,h=None,b=None: tools.http_request(m,u,h,b),
                                    lambda c,timeout=None: tools.execute_terminal(c,timeout=timeout),
                                )
                                if verdict:
                                    messages.append({"role":"user","content":verdict})
                        except Exception:
                            pass

                    if t_name == "claim_flag":
                        break
                else:
                    messages.append({"role":"user","content":"SYSTEM: No valid tool block. Continue or finalize."})

                # Supervisor check every 5 turns
                if turn % 5 == 0:
                    try:
                        usage = providers.get_usage()
                        cost = float(usage.get("est_cost_usd", 0))
                        if cost >= float(config.get("cost_hard_cap_usd", 2.0)):
                            console.print("[red]Cost hard cap reached — aborting phase.[/red]")
                            break
                    except Exception:
                        pass

                # Heuristic: if no tool in last 2 responses, agent is stuck
                recent = messages[-4:]
                tool_count = sum(1 for m in recent if m["role"] == "assistant" and _extract_tool(m["content"]))
                if turn > 3 and tool_count == 0:
                    console.print("[yellow]  Agent appears stuck — moving to next phase.[/yellow]")
                    break

            # Only mark complete if success criteria actually met
            if phase_success:
                task_graph.mark(phase["id"], "complete")
            elif turn > max_turns:
                task_graph.mark(phase["id"], "failed")
            else:
                task_graph.mark(phase["id"], "exhausted")
            if flag_captured:
                break

        if flag_captured:
            break

    # Summary of final phase states
    console.print(f"\n[bold cyan]{'─'*50}[/bold cyan]")
    console.print(Panel(task_graph.summary(), title="Final Phase Status"))
    console.print(Panel(chainer.get_chain_summary(), title="Attack Chains"))
    if flag_captured:
        console.print("[bold green]🐡 Fugu mission complete — flag captured.[/bold green]")
    else:
        console.print("[bold yellow]🐡 Fugu mission ended — flag not captured.[/bold yellow]")
    console.print(f"[dim]{format_spend(providers.get_usage())}[/dim]")
