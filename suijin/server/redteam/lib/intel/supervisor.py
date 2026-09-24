"""
suijin/supervisor.py
======================
Cost-aware MISSION SUPERVISOR for the Red Team agent.

Why this exists
---------------
The red-team loop in ``redteamer.py`` is a synchronous Thought -> Action ->
Observation cycle with no self-monitoring. When the LLM loses context it
loops in circles, re-runs the same payloads, and drifts off the objective —
which wastes API credits and is dangerous for enterprise use.

The supervisor is a *cheaper* model that the main loop consults every N
cycles (an "inline checkpoint" — not a background thread, because the loop
is synchronous). It looks at recent activity, decides whether the agent is
stuck / looping / off-mission / over-budget, and returns a verdict. When it
fires, the main loop injects a corrective ``SUPERVISOR OVERRIDE`` directive.

Design notes
------------
* **Cheap by default.** An LLM call is only made when a cheap heuristic
  trips, or periodically as a deep check — see ``evaluate()``. Most
  checkpoints cost zero tokens.
* **Decoupled.** This module does NOT import ``redteamer`` (that would be a
  circular import). It reads ``thoughts.json`` directly and is handed the
  live ``messages`` list by the caller.
* **Reuses existing logic.** Drift detection is delegated to
  ``drift_analyser.analyse_drift`` rather than reinvented.
"""

import json
import re
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

_llm = __import__("suijin.modules.loader", fromlist=["load_local_module"])
load_local_module = _llm.load_local_module

BASE_DIR = Path(__file__).resolve().parent
THOUGHTS_PATH = BASE_DIR / "thoughts.json"

console = Console()

# Centralized force-load — shares ONE providers instance with redteamer
providers = load_local_module("providers")
drift_analyser = load_local_module("drift_analyser")
generate = providers.generate


def set_providers(mod):
    """Inject the caller's providers module so the supervisor shares ONE
    token/cost accumulator with the main loop.

    Each module that force-loads providers gets its own instance with its own
    USAGE dict. redteamer accumulates spend in *its* instance; without this
    injection the supervisor would read a different (always-zero) tally and
    its own LLM calls wouldn't count toward the total. redteamer calls this
    right after importing the supervisor.
    """
    global providers, generate
    providers = mod
    generate = mod.generate


SUPERVISOR_PROMPT = """
# ROLE: COST-AWARE MISSION SUPERVISOR
You oversee an autonomous offensive-security agent. You do NOT attack
yourself. Your only job is to keep the agent on-mission and stop it wasting
API credits.

# WHAT YOU RECEIVE
- The ORIGINAL objective.
- The agent's recent reasoning (its own words).
- Its recent tool actions and (truncated) results.
- Automatic drift flags and cheap heuristic flags.
- Spend so far (estimated USD and token counts).

# WHEN TO INTERVENE
Mark the agent as stuck/off-mission if ANY of these are true:
- It repeats the same or near-identical tool calls without new information.
- It keeps hitting the same errors and is not adapting.
- Its reasoning has drifted away from the original objective.
- It is spending credits on expensive, low-yield activity (e.g. brute-force
  subdomain enumeration) while obvious low-hanging fruit (e.g. an injectable
  search/login parameter) is untested.

# WHAT TO RETURN
Respond with EXACTLY ONE JSON object and nothing else:
{
  "stuck": true|false,
  "reason": "one short sentence",
  "new_directive": "a concrete next instruction for the agent, or empty string",
  "switch_to_low_hanging": true|false,
  "recommend_abort": true|false
}
- "new_directive" must be specific and actionable (name the endpoint/param/
  technique to try next, or what to STOP doing).
- Set "switch_to_low_hanging" when the agent should drop an expensive avenue
  and target the cheapest likely vulnerability instead.
- Set "recommend_abort" only if continuing is pointless (objective already
  met, or no viable path remains).
"""


# ----------------------------------------------------------------------
# Telemetry collection
# ----------------------------------------------------------------------
_TOOL_RE = re.compile(r'\{[\s\S]*?"tool"[\s\S]*?\}')


def _extract_action(assistant_text):
    """Pull a short 'tool: arg' descriptor out of an assistant turn, or None."""
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", assistant_text)
    raw = m.group(1) if m else None
    if raw is None:
        m = _TOOL_RE.search(assistant_text)
        raw = m.group(0) if m else None
    if raw is None:
        return None
    try:
        raw = re.sub(r",\s*\}", "}", raw)
        data = json.loads(raw)
    except Exception:
        return None
    tool = data.get("tool", "unknown")
    args = data.get("args") or data.get("parameters") or {}
    detail = ""
    if isinstance(args, dict):
        detail = str(
            args.get("cmd")
            or args.get("command")
            or args.get("url")
            or args.get("keyword")
            or args.get("file_path")
            or args
        )
    return f"{tool}: {detail}".strip()


def _read_recent_thoughts(limit=5):
    """Return the last `limit` plan_and_reasoning strings from thoughts.json."""
    if not THOUGHTS_PATH.exists():
        return []
    try:
        trail = json.loads(THOUGHTS_PATH.read_text(encoding="utf-8"))
        return [t.get("plan_and_reasoning", "") for t in trail[-limit:]]
    except Exception:
        return []


def collect_telemetry(messages, turn, objective, usage, window=6):
    """Build a compact snapshot of what the agent has been doing lately."""
    actions, results = [], []
    for msg in messages:
        content = msg.get("content", "")
        if msg.get("role") == "assistant":
            act = _extract_action(content)
            if act:
                actions.append(act)
        elif msg.get("role") == "user" and content.startswith("Result:"):
            results.append(content[len("Result:") :].strip())

    recent_actions = actions[-window:]
    recent_results = results[-window:]
    thoughts = _read_recent_thoughts(5)

    # Reuse the existing drift detector on the full action list so its
    # low_goal_overlap rule (which needs index > 2) can fire.
    drift = (
        drift_analyser.analyse_drift(objective, actions)
        if actions
        else {
            "drift_detected": False,
            "drift_count": 0,
            "drift_causes": [],
            "suggestions": [],
        }
    )

    # Cheap repetition / error signals.
    dup_count = 0
    if recent_actions:
        dup_count = max(recent_actions.count(a) for a in recent_actions)
    error_count = sum(1 for r in recent_results if r.startswith("Error") or "[STDERR]" in r or "Routing Error" in r)

    return {
        "turn": turn,
        "objective": objective,
        "total_actions": len(actions),
        "recent_actions": recent_actions,
        "recent_results": [r[:300] for r in recent_results],
        "recent_thoughts": thoughts,
        "drift": drift,
        "max_repeat": dup_count,
        "error_count": error_count,
        "usage": usage,
    }


# ----------------------------------------------------------------------
# Cheap heuristic pre-filter (no LLM call)
# ----------------------------------------------------------------------
def heuristic_stuck_check(telemetry, config):
    """Return a list of cheap flags suggesting the agent may be stuck."""
    flags = []
    if telemetry["max_repeat"] >= 3:
        flags.append("repeated_action")
    if telemetry["error_count"] >= 3:
        flags.append("repeated_errors")
    if telemetry["drift"].get("drift_detected"):
        flags.append("drift")

    cost = float(telemetry["usage"].get("est_cost_usd", 0.0))
    _alert = float(config.get("cost_alert_usd", 0.25) or 0.0)
    if _alert > 0 and cost >= _alert:  # 0/negative = alert disabled (operator: no caps)
        flags.append("cost_alert")
    return flags


# ----------------------------------------------------------------------
# LLM verdict
# ----------------------------------------------------------------------
def _parse_verdict(text):
    """Tolerantly parse the supervisor's JSON verdict."""
    default = {
        "stuck": False,
        "reason": "",
        "new_directive": "",
        "switch_to_low_hanging": False,
        "recommend_abort": False,
    }
    if not text:
        return default
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return default
    try:
        raw = re.sub(r",\s*\}", "}", m.group(0))
        data = json.loads(raw)
    except Exception:
        return default
    out = dict(default)
    for k in out:
        if k in data:
            out[k] = data[k]
    return out


def supervisor_review(objective, config, telemetry, flags):
    """Ask the cheap supervisor model for a verdict on the telemetry."""
    model_id = config.get("supervisor_model_id") or config.get("sentinel_model_id")
    u = telemetry["usage"]
    user_msg = (
        f"ORIGINAL OBJECTIVE:\n{objective}\n\n"
        f"TURN: {telemetry['turn']}   "
        f"SPEND: ~${u.get('est_cost_usd', 0):.4f} "
        f"({u.get('input_tokens', 0)} in / {u.get('output_tokens', 0)} out tokens)\n\n"
        f"HEURISTIC FLAGS: {', '.join(flags) or 'none'}\n"
        f"DRIFT: {telemetry['drift'].get('drift_count', 0)} event(s)\n\n"
        f"RECENT REASONING:\n- " + "\n- ".join(telemetry["recent_thoughts"][-3:] or ["(none)"]) + "\n\n"
        "RECENT ACTIONS:\n- " + "\n- ".join(telemetry["recent_actions"] or ["(none)"]) + "\n\n"
        "RECENT RESULTS (truncated):\n- " + "\n- ".join(telemetry["recent_results"] or ["(none)"]) + "\n\n"
        "Return your JSON verdict now."
    )
    messages = [
        {"role": "system", "content": SUPERVISOR_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    try:
        resp = generate(messages, config, model_id=model_id, temperature=0.1, max_tokens=300)
    except Exception as e:
        console.print(f"[yellow]Supervisor call failed: {e}[/yellow]")
        return _parse_verdict(None)
    if isinstance(resp, str) and resp.startswith("Error:"):
        return _parse_verdict(None)
    return _parse_verdict(resp)


# ----------------------------------------------------------------------
# Top-level entry point used by the main loop
# ----------------------------------------------------------------------
def evaluate(messages, turn, objective, config):
    """
    Run one supervisor checkpoint.

    Returns (verdict, telemetry, flags). An LLM call is only made when a
    cheap heuristic trips OR on a periodic 'deep' checkpoint (every
    2 * supervisor_interval cycles). Cost-budget thresholds are folded into
    the verdict so the caller can act on them uniformly.
    """
    usage = providers.get_usage()
    telemetry = collect_telemetry(messages, turn, objective, usage)
    flags = heuristic_stuck_check(telemetry, config)

    interval = int(config.get("supervisor_interval", 5))
    deep = turn % max(interval * 2, 1) == 0

    if flags or deep:
        verdict = supervisor_review(objective, config, telemetry, flags)
    else:
        verdict = _parse_verdict(None)
        verdict["skipped"] = True

    # ---- Heuristics are AUTHORITATIVE for clear loops ----
    # The cheap supervisor model is unreliable at strict JSON and sometimes
    # returns "not stuck" (or junk) even on an obvious loop. When our cheap,
    # deterministic checks already PROVE the agent is stuck — the same command
    # repeated, or errors piling up — force an intervention without needing the
    # model's blessing. This is the real safety net; the LLM only adds nuance.
    if ("repeated_action" in flags) or ("repeated_errors" in flags):
        verdict["stuck"] = True
        if not verdict.get("reason"):
            verdict["reason"] = "Repeating failed commands — breaking the loop."
        if not verdict.get("new_directive"):
            verdict["new_directive"] = (
                "STOP repeating failed commands and STOP trying to install tools "
                "(no nmap/masscan/brew/apt). Those tools are not available. The "
                "target is a web application — interact with it directly using the "
                "http_request tool (GET/POST), or write a Python script with the "
                "requests library via write_file and run it. Start by testing the "
                "login and search forms for SQL injection."
            )

    # ---- Cost guardrail (deterministic, independent of the LLM) ----
    # 0/negative = DISABLED (operator: no spending caps). Without the > 0
    # guard a 0 default made `cost >= 0` always true — instant abort
    # steering on every check for fresh configs missing the keys.
    cost = float(usage.get("est_cost_usd", 0.0))
    budget = float(config.get("cost_budget_usd", 1.0) or 0.0)
    hard_cap = float(config.get("cost_hard_cap_usd", 2.0) or 0.0)
    if hard_cap > 0 and cost >= hard_cap:
        verdict["recommend_abort"] = True
        verdict["stuck"] = True
        if not verdict.get("reason"):
            verdict["reason"] = f"Hard cost cap ${hard_cap:.2f} reached (spent ~${cost:.4f})."
    elif budget > 0 and cost >= budget:
        verdict["switch_to_low_hanging"] = True
        verdict["stuck"] = True
        if not verdict.get("reason"):
            verdict["reason"] = f"Budget ${budget:.2f} exceeded — switch to low-hanging fruit."

    return verdict, telemetry, flags


def format_spend(usage):
    """One-line spend readout for the operator log."""
    est = "~" if usage.get("priced") else "?"
    return (
        f"Spend: {est}${usage.get('est_cost_usd', 0):.4f}  |  "
        f"{usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out tokens  |  "
        f"{usage.get('calls', 0)} calls"
    )


def render_panel(verdict, flags, usage):
    """Render the supervisor's intervention as a Rich panel."""
    directive = verdict.get("new_directive") or "(steer toward simplest untested vulnerability)"
    body = (
        f"[bold]Reason:[/bold] {verdict.get('reason', '')}\n"
        f"[bold]Flags:[/bold] {', '.join(flags) or 'periodic deep check'}\n"
        f"[bold]Directive:[/bold] {directive}\n"
        f"[dim]{format_spend(usage)}[/dim]"
    )
    console.print(Panel(body, title=" SUPERVISOR OVERRIDE", border_style="magenta"))
