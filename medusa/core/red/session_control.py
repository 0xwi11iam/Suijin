"""
medusa/core/red/session_control.py — Runtime commands & helpers for Red Team.

Extracted from redteamer.py. Handles report generation, audit printing,
state summary, attack chain building, session listing, template browsing,
and objective file loading.
"""
from __future__ import annotations

import os

from rich.console import Console

console = Console()


def force_report(agent, thread_id, final_state, objective, config):
    """Generate a full engagement report on demand."""
    from medusa.modules.loader import load_local_module
    from medusa.tools.audit_trail import get_audit_json
    from medusa.tools.report_exporter import generate_report
    providers = load_local_module("providers")
    state = final_state or agent.get_state(thread_id) or {}
    trace = state.get("execution_trace", [])
    findings = get_audit_json().get("findings", [])
    path = generate_report(
        engagement_name=objective[:80],
        execution_trace=trace,
        findings=findings,
        target_info=state.get("target_info", {}),
        messages=state.get("messages", []),
        cost_usd=providers.USAGE.get("est_cost_usd", 0),
        completion_reason=state.get("completion_reason", ""),
        attack_chains=build_attack_chains(trace),
    )
    console.print(f"[green]Report saved: {path}[/green]")
    # Also end audit trail
    from medusa.modules.loader import load_local_module as _llm
    from medusa.tools.audit_trail import end_audit
    end_audit(_llm("providers").USAGE.get("est_cost_usd", 0))


def print_audit_trail():
    """Print a summary of the current audit trail."""
    from medusa.tools.audit_trail import get_audit_json
    trail = get_audit_json()
    if not trail or not trail.get("iterations"):
        console.print("[dim]No audit trail data yet.[/dim]")
        return
    console.print(f"[bold]Audit Trail: {len(trail['iterations'])} iterations, "
                  f"{len(trail.get('findings', []))} findings[/bold]")


def print_state_summary(agent, thread_id):
    """Print current agent state summary."""
    state = agent.get_state(thread_id)
    if not state:
        console.print("[dim]No state available.[/dim]")
        return
    phase = state.get("current_phase", "?")
    iters = state.get("current_iteration", 0)
    msgs = len(state.get("messages", []))
    console.print(f"[bold]State:[/bold] phase={phase}, iters={iters}, msgs={msgs}")


def build_attack_chains(trace: list) -> list:
    """Build attack chains from execution trace for Mermaid diagrams."""
    chains = []
    current_chain = {"steps": []}
    for step in trace:
        tn = step.get("tool_name", "?")
        success = step.get("success", True)
        label = f"{tn} ({'OK' if success else 'FAIL'})"
        current_chain["steps"].append(label)
        if step.get("completion_reason"):
            chains.append(current_chain)
            current_chain = {"steps": []}
    if current_chain["steps"]:
        chains.append(current_chain)
    return chains


def list_sessions():
    """List saved sessions for replay."""
    from medusa.tools.session_replay import list_sessions as _list
    sessions = _list()
    if not sessions:
        console.print("[dim]No saved sessions.[/dim]")
        return
    console.print("[bold]Saved Sessions:[/bold]")
    for i, s in enumerate(sessions[:10], 1):
        summary = s.get("state_summary", {})
        console.print(f"  {i}. [{s.get('saved_at','?')}] {s.get('objective','?')[:60]}")
        console.print(f"     phase={summary.get('phase','?')}, iters={summary.get('iterations',0)}, cost=${s.get('cost_usd',0):.4f}")


def handle_template(config):
    """Interactive template browser — view, load, or create engagement templates."""
    from medusa.core.templates import list_templates, load_template, save_template
    templates = list_templates()
    console.print(f"\n[bold]Available Templates ({len(templates)}):[/bold]")
    for i, t in enumerate(templates, 1):
        tmpl = load_template(t)
        console.print(f"  [bold]{i}.[/bold] [cyan]{t}[/cyan] — {tmpl.get('description','')[:80]}")
    console.print(f"  [bold]{len(templates)+1}.[/bold] [yellow]New template (AI designs one)[/yellow]")
    console.print(f"  [bold]{len(templates)+2}.[/bold] [dim]Cancel[/dim]")
    try:
        choice = console.input("\n[bold cyan]  Select template  [/bold cyan]").strip()
        idx = int(choice) - 1
        if 0 <= idx < len(templates):
            name = templates[idx]
            tmpl = load_template(name)
            config["template"] = name
            config.update({k: tmpl[k] for k in ["ports", "wordlists", "checks", "max_iterations", "headless"] if k in tmpl})
            console.print(f"[green]  Loaded template: {name}[/green]")
            console.print(f"  Ports: {tmpl.get('ports',[])} | Checks: {tmpl.get('checks',[])} | Max iters: {tmpl.get('max_iterations','?')}")
        elif idx == len(templates):
            # AI designs a new template
            console.print("[dim]  Describe the template you want (e.g. 'quick WordPress scan with SQLi and XSS'):[/dim]")
            desc = console.input("[bold cyan]  Description  [/bold cyan]").strip()
            if desc:
                name = desc.replace(" ", "_").lower()[:30]
                new_tmpl = {
                    "name": desc[:60], "description": desc,
                    "ports": [80, 443], "wordlists": ["common.txt"],
                    "tools": ["nmap", "whatweb", "gobuster"],
                    "checks": ["sqli", "xss"],
                    "max_iterations": 25, "headless": False,
                }
                # AI-enhanced: if keywords match, expand config
                desc_lower = desc.lower()
                if "api" in desc_lower:
                    new_tmpl["ports"].extend([3000, 5000, 8000])
                    new_tmpl["checks"].extend(["jwt", "cors", "mass_assignment"])
                if "spa" in desc_lower or "react" in desc_lower or "javascript" in desc_lower:
                    new_tmpl["headless"] = True
                if "wordpress" in desc_lower:
                    new_tmpl["ports"].append(8080)
                    new_tmpl["checks"].extend(["sqli", "file_upload"])
                if "cloud" in desc_lower:
                    new_tmpl["checks"].extend(["ssrf", "information_disclosure", "subdomain_takeover"])
                if "full" in desc_lower or "everything" in desc_lower:
                    new_tmpl = load_template("full_assault")
                path = save_template(name, new_tmpl)
                config["template"] = name
                config.update({k: new_tmpl[k] for k in ["ports", "wordlists", "checks", "max_iterations", "headless"] if k in new_tmpl})
                console.print(f"[green]  Template saved: {path}[/green]")
        else:
            console.print("[dim]  Cancelled.[/dim]")
    except (ValueError, KeyboardInterrupt, EOFError):
        console.print("[dim]  Cancelled.[/dim]")


def load_objective_from_file(raw_path: str) -> str | None:
    """Load objective text from a file. Handles drag-drop paths with quotes/spaces.

    Supports: .txt, .md, .rtf (rich text stripped to plain text).
    Returns the objective string or None on failure.
    """
    # Clean drag-drop artifacts: quotes, escaped spaces, trailing whitespace
    path = raw_path.strip()
    # Remove surrounding quotes (single or double)
    if (path.startswith('"') and path.endswith('"')) or \
       (path.startswith("'") and path.endswith("'")):
        path = path[1:-1]
    # Handle escaped spaces from drag-drop
    path = path.replace("\\ ", " ")

    # Resolve ~ and relative paths
    path = os.path.expanduser(path)
    path = os.path.abspath(path)

    if not os.path.exists(path):
        console.print(f"[bold red]File not found:[/] {path}")
        return None
    if not os.path.isfile(path):
        console.print(f"[bold red]Not a file:[/] {path}")
        return None

    ext = os.path.splitext(path)[1].lower()
    console.print(f"[dim]Loading: {path} ({ext})[/]")

    try:
        if ext == '.rtf':
            text = _strip_rtf(path)
        else:
            # .txt, .md, or anything else — read as plain text
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
    except Exception as e:
        console.print(f"[bold red]Read error:[/] {e}")
        return None

    text = text.strip()
    if not text:
        console.print("[bold red]File is empty.[/]")
        return None

    console.print(f"[dim]Extracted {len(text)} characters.[/]")
    return text


def _strip_rtf(path: str) -> str:
    """Extract plain text from an RTF file. Falls back to raw if no rtf parser."""
    try:
        from striprtf.striprtf import rtf_to_text
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return rtf_to_text(f.read())
    except ImportError:
        pass
    # Fallback: strip RTF tags with regex
    import re
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    # Remove RTF control words and groups
    content = re.sub(r'\\[a-z]+\d*', '', content)    # control words
    content = re.sub(r'\\[{}]', '', content)          # escaped braces
    content = re.sub(r'[{}]', '', content)             # group braces
    content = re.sub(r'\\\n', '\n', content)           # line continuations
    return content.strip()
