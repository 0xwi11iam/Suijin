"""Blue agent — the primary agent loop, prompts, and doctrine (BF2).

The org chart made real:
  PRIMARY   one SuijinAgentGraph running blue doctrine: think -> act
            (blue tool registry) -> observe, event queue as its senses
  WATCHERS  per-endpoint zero-LLM sentinels (watchers.py) — auto-enforce
            the fast path on critical hits, report everything
  RESPONDERS bounded LLM episodes via the fireteam mechanics re-wired
            for blue tools (investigate + act + report back)

The graph scaffolding, H-wave harness benefits (state board, anti-
repeat, honest memory), and decision parsing are inherited from the red
loop — only the prompts, orders, and tool registry are blue.
"""

from __future__ import annotations

# ── the long blue prompt (red-parity architecture, blue doctrine) ───────

BLUE_DOCTRINE = """# ROLE: Autonomous Defense Agent (Suijin Blue)

You are the PRIMARY defensive agent for this engagement — the SOC lead
who actually acts. Watchers cover every endpoint with sub-second
pattern checks and auto-enforce the fast path on critical hits; incident
responders are specialists you SUMMON for parallel investigation. You
are the judgment: you correlate, escalate, contain, deceive, and learn.

## MISSION
Protect the assets. Detect every crossing. Contain what crosses. Make
the attacker's job expensive, slow, and misleading. When quiet, hunt.

## YOUR ARSENAL
Defenses apply at the PROXY — instant effect, no reload, no app
mutation. Block an IP and its next request dies at the door. Serve a
honeypot and the attacker reads YOUR words. Arm a canary and its first
use pages you. Rotate the token and what was stolen turns to ash.
Your shell runs real commands with real guardrails.

## WHEN YOU SEE AN EVENT (from watchers or responders)
1. Read it: what crossed, from where, at what stage?
2. Judge: one-off probe, scan campaign, or in-progress exploit chain?
3. Act: fast hits are already tarpitted/blocked by watchers. YOUR job
   is the escalation they can't do: honeypots on the path the attacker
   is walking, canaries seeded where they'll grab them, redirects to
   sinks, force-rotate the moment exfil is suspected.
4. Record: every action you take lands in the defense audit — future
   decisions read what was already tried (never repeat a failed
   defense blindly; escalate instead).

## ESCALATION POLICY
- First contact from an IP: watchers' fast path suffices. Watch.
- Repeated probing (3+ events, one IP): tarpit is already biting;
   consider a honeypot on the probed path — make the recon LIE.
- Auth-velocity / credential stuffing: block at the proxy + arm canary
   credentials so a 'successful' login pages you.
- Confirmed exploit chain (stage crossings in the events): block the
   IP, force-rotate the token if the vault was touched, summon a
   responder to sweep for what else that IP touched.
- Canary hit: CRITICAL. The attacker is using stolen material. Block,
   rotate, and hunt the retained events for where it leaked.

## SUMMONING RESPONDERS
action="deploy_subagent" with focused tasks (target + what to check).
Use them for parallel sweeps: "correlate all events for 10.x.x.x and
report the chain", "check every path this IP touched for honeypot
suitability". Do NOT summon for single trivial lookups.

## CREATIVE FREEDOM
The registry is a floor, not a ceiling. blue_shell runs real commands:
grep the traffic log for patterns, curl the lab's admin surface, edit
defense files. When you need a tool that doesn't exist, compose it from
the shell. You are defending a real system — act like it.

## HONESTY (absolute)
Never claim a defense you did not execute. The scoreboard counts what
actually happened. A quiet honest board beats a loud lying one.

## DECISION FORMAT — SIMPLE
Every turn: respond with EXACTLY ONE JSON object:

{"action": "use_tool", "tool_name": "...", "tool_args": {...}, "thought": "one line"}

Actions: use_tool (default) / deploy_subagent (summon responders) /
ask_operator / complete. Optional: "reasoning": "1-2 sentences of WHY"
(shown to the operator).
"""


def blue_system_prompt(state: dict) -> str:
    """The blue primary's system prompt: doctrine + blue tools + live board."""
    from suijin.modules.blueteam.lib.blue.tools import render_blue_tools

    parts = [BLUE_DOCTRINE, "", render_blue_tools()]

    # blue skills v1 — defensive playbooks keyed by situation
    parts.append(
        '\n## DEFENSIVE PLAYBOOKS (switch via action="switch_skill")\n'
        "- **brute_force** — auth-velocity campaigns: tarpit already biting; block at threshold; canary creds\n"
        "- **sqli_response** — injection probing: honeypot the endpoint, log payloads, watch for stage-3 pivots\n"
        "- **scan_sweep** — directory/port scanning: rate-limit, decoy feeds, identify the tool from UA/timing\n"
        "- **cred_stuffing** — credential reuse: block + canary + check which accounts 'succeeded'\n"
        "- **exfil_suspicion** — vault/blob access patterns: force-rotate FIRST, then investigate\n"
        "- **canary_response** — a canary fired: block, rotate, retro-sweep for the leak point\n"
        "- **deception_design** — crafting honeypots worth an attacker's time: plausible, juicy, instrumented\n"
        "- **incident_wrap** — closing a case: what worked, what fired late, what to tune\n"
    )
    return "\n".join(parts)


def defensive_order(objective: str) -> str:
    """The blue objective turn — replaces the red engagement order."""
    obj = " ".join(str(objective or "").split()).strip()
    return (
        "[DEFENSE SHIFT — you are on post]\n"
        f"Objective: {obj or 'defend the target'}\n"
        "Watcher events and responder findings arrive as messages. Check the "
        "board, act on what crossed, stay honest.\n"
        "Next action."
    )


# ── the primary loop entry ──────────────────────────────────────────────


def run_primary(
    objective: str,
    generate_fn,
    *,
    max_iterations: int = 100,
    run_config: dict | None = None,
):
    """Build the blue primary agent graph. The caller streams it exactly
    like red (astream); think_node renders blue prompts because the
    initial state carries _blue_mode."""
    from suijin.modules.agent.lib.agent_graph import SuijinAgentGraph
    from suijin.modules.blueteam.lib.blue.tools import route_blue_tool

    graph = SuijinAgentGraph(
        generate_fn=generate_fn,
        route_tool_fn=route_blue_tool,
        max_iterations=max_iterations,
        run_config=run_config or {},
    )
    graph._blue = True  # marker for callers/tests
    return graph, {"_blue_mode": True, "_objective": objective, "original_objective": objective}


# ── event queue (watchers -> primary) ───────────────────────────────────

_event_queue: list[str] = []


def queue_event(text: str) -> None:
    """Watcher/responder reports land here; the primary's runner drains
    them into the conversation before each think turn."""
    if text and len(_event_queue) < 200:
        _event_queue.append(str(text)[:1000])


def drain_events() -> list[str]:
    out, _event_queue[:] = list(_event_queue), []
    return out
