"""The swarm worker loop — ONE implementation, used by every worker.

The 2026-11-04 field failure class (three bugs, one root cause):
  1. the model was never shown the tool catalog → it replied in prose
  2. a prose reply (no JSON) was treated as {"action": "complete"} →
     intents marked done with ZERO tool calls (7 burned in seconds)
  3. `decision` was assigned inside `with contextlib.suppress(Exception)`
     → any parse raise left it unbound → UnboundLocalError killed the agent

The Decision boundary rule (suijin/server/agent/lib/decision.py) applies
here too: parse exactly once, tolerate model drift in ONE place, and
never let a parse failure masquerade as completion.
"""

from __future__ import annotations

import contextlib

SYSTEM_TEMPLATE = (
    "You are a worker agent on an authorized bug bounty engagement. "
    "Your ONLY task is the intent below. Work it to completion using the "
    "available tools — really run them, do not describe what you would run.\n\n"
    "{catalog}\n\n"
    "Every reply must be ONE JSON object:\n"
    '  {{"action": "use_tool", "tool_name": "<name from the catalog>", '
    '"args": {{"...": "..."}}, "thought": "..."}}\n'
    'or when the intent is fully worked:\n'
    '  {{"action": "complete", "reason": "...", "facts": [...]}}\n'
    "Prose-only replies are errors and will be rejected."
)


def _session_hint() -> str:
    """One line telling workers which authenticated sessions exist.

    The operator logs in via `suijin auth` (or the live jar); agents
    never see values — names only, and the model passes the name to
    http_request/http_replay via credential='<name>'. Without this
    hint the sessions sat unused because no prompt mentioned them."""
    with contextlib.suppress(Exception):
        import json as _json

        from suijin.modules.credential_store.main import creds_list

        raw = str(creds_list())
        data = _json.loads(raw) if raw.strip().startswith("{") else {}
        names = [n for n in (data.keys() if isinstance(data, dict) else [])]
        if names:
            shown = ", ".join(sorted(names)[:8])
            return (
                f"\n\nAUTHENTICATED SESSIONS available (pass credential='<name>' "
                f"to http_request / http_replay): {shown}"
            )
    return ""


def _load_catalog() -> str:
    """The full tool catalog (how-to-call header + every tool). The model
    cannot call tools it cannot see — this is not optional context."""
    try:
        from suijin.modules.tools.lib import dispatch

        return str(dispatch.get_tool_catalog())
    except Exception:  # noqa: BLE001 — degrade to a hint, never crash the loop
        return "(tool catalog unavailable — ask the operator if you need a tool)"


def _balanced_objects(text: str) -> list[str]:
    """Every top-level {...} block, brace-balanced and string-aware.

    A regex cannot do this: a pattern like brace-class-nesting collapses
    to an inner object when args nest two levels deep (e.g.
    {"headers": {"a": {"b": 1}}}) — the model's real output shape.
    """
    objs: list[str] = []
    depth = 0
    start: int | None = None
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                objs.append(text[start : i + 1])
                start = None
    return objs


def _looks_like_decision(obj: dict) -> bool:
    return any(k in obj for k in ("action", "tool_name", "tool"))


def _parse_decision(response: str) -> dict | None:
    """Extract the model's decision JSON.

    Fenced ```json blocks win; otherwise the LAST balanced object that
    parses AND looks like a decision (a prose preamble followed by a
    decision object is common — an inline EXAMPLE earlier in the reply
    must not win over the real decision at the end).
    Returns None when nothing parses — the caller re-prompts, it NEVER
    treats None as completion.
    """
    import json as _json
    import re as _re

    def _loads(s: str) -> dict | None:
        try:
            parsed = _json.loads(s)
            return parsed if isinstance(parsed, dict) else None
        except Exception:  # noqa: BLE001 — candidate screening, not control flow
            return None

    m = _re.search(r"```json\s*(.*?)\s*```", response, _re.S)
    if m:
        obj = _loads(m.group(1))
        if obj is not None and _looks_like_decision(obj):
            obj = _normalize(obj)
            if obj is not None:
                return obj

    candidates = [_loads(c) for c in _balanced_objects(response)]
    for obj in reversed(candidates):
        if obj is not None and _looks_like_decision(obj):
            return _normalize(obj)
    for obj in reversed(candidates):
        if obj is not None:
            return _normalize(obj)
    return None


def _normalize(obj: dict) -> dict | None:
    from suijin.server.agent.lib.decision import normalize_decision

    try:
        return normalize_decision(obj)
    except Exception:  # noqa: BLE001 — normalize is drift-tolerant; belt and braces
        return None


REprompt_TOOL = (
    "Your last reply was prose or had no parseable decision. Reply with ONE "
    'JSON object only: {"action": "use_tool", "tool_name": "<name from the '
    'catalog>", "args": {...}} — or {"action": "complete", "reason": "..."} '
    "if the intent is genuinely finished."
)

REprompt_TRUNCATED = (
    "Your last reply was CUT OFF before the JSON closed (your command was too "
    "long for one reply). Split the work: write long scripts/payloads to disk "
    'with write_file first, then reply with a SHORT call — {"action": '
    '"use_tool", "tool_name": "execute_terminal", "args": {"cmd": '
    '"python3 script.py"}}. Keep every reply well under the length limit.'
)


def execute_intent(
    identity: str,
    intent: dict,
    bus=None,
    max_turns: int = 0,
    on_event=None,
    on_turn=None,
) -> dict:
    """Work one intent: LLM → decision → tool → result → repeat.

    Returns {"state": done|blocked|exhausted, "facts": [...], "note": str}.

    on_turn(turn) → optional str: called at the TOP of every turn; a
    non-None return is injected into the conversation as an OPERATOR
    INTERJECTION user message (this is how `suijin swarm say` reaches
    agents mid-intent instead of just printing to their log).

    max_turns=0 → read `swarm_turn_cap` from config. DEFAULT: NO CAP
    (operator ruling, 2026-11-06 — "by default no turn cap"; a cap is an
    explicit setting, not an assumption). 0/absent = unlimited. The
    strike rules and provider errors still bound runaway loops.
    """
    import time

    if max_turns <= 0:
        try:
            from suijin.modules.platform.lib.config_loader import load_config

            cap = int(load_config().get("swarm_turn_cap") or 0)
            max_turns = cap if cap > 0 else 100000  # no cap configured = effectively unlimited
        except Exception:  # noqa: BLE001 — config read must never kill the loop
            max_turns = 100000

    messages = [
        {"role": "system", "content": SYSTEM_TEMPLATE.format(catalog=_load_catalog()) + _session_hint()},
        {"role": "user", "content": f"INTENT: {intent['text']}\n\nExecute this intent."},
    ]

    facts: list[dict] = []
    parse_fails = 0

    for turn in range(max_turns):
        # operator interjection (swarm say) — lands mid-conversation
        if on_turn is not None:
            with contextlib.suppress(Exception):
                interjection = on_turn(turn)
                if interjection:
                    messages.append(
                        {
                            "role": "user",
                            "content": f"OPERATOR INTERJECTION: {interjection} — incorporate this into your current work.",
                        }
                    )

        # --- model call -------------------------------------------------
        try:
            from suijin.modules.platform.lib.config_loader import load_config, load_env

            load_env()  # pick up the operator's .env (API keys)
            from suijin.modules.providers.lib import generate

            cfg = load_config()
            cfg["max_tokens_per_request"] = max(
                int(cfg.get("max_tokens_per_request") or 0), 8000
            )  # long shell payloads truncated mid-JSON = the dominant
            # unparseable-reply cause; 4k cut real probes off
            cfg["intelligence"] = "medium"  # workers don't need max thinking
            cfg["response_format_json"] = True  # proven accepted by z.ai
            # (2026-11-06 probe) — worker replies are always ONE JSON
            # decision; enforcement kills the strike class at the source
            response = generate(messages, cfg)
        except Exception as e:  # noqa: BLE001 — provider errors become 'blocked'
            return {"state": "blocked", "facts": facts, "note": f"provider error: {e}"}

        if not isinstance(response, str) or response.startswith("Error:"):
            return {"state": "blocked", "facts": facts, "note": str(response)[:200]}

        # --- decision (parsed EXACTLY ONCE, failure ≠ completion) --------
        decision = _parse_decision(response)
        if decision is None:
            parse_fails += 1
            # truncation is the dominant failure: the model writes a long
            # shell one-liner, the reply is cut mid-JSON, and every retry
            # repeats it. The corrective teaches the split: file first,
            # short command second.
            truncated = ("tool_name" in response or response.lstrip().startswith("{")) and not response.rstrip().endswith("}")
            print(
                f"[{identity}] strike {parse_fails}: unparseable reply "
                f"(truncated={truncated}, head: {response[:120]!r})",
                flush=True,
            )
            if parse_fails >= 5:
                return {"state": "blocked", "facts": facts, "note": "model never emitted a JSON decision (5 strikes)"}
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": REprompt_TRUNCATED if truncated else REprompt_TOOL})
            continue

        action = str(decision.get("action") or "use_tool")

        if action == "complete":
            reason = str(decision.get("reason") or decision.get("completion_reason") or "done")
            return {"state": "done", "facts": facts, "note": reason[:200]}

        tool_name = str(decision.get("tool_name") or "").strip()
        tool_args = decision.get("tool_args") or {}

        if not tool_name:
            # plan_tools / ask_operator / write_note-only are legitimate
            # decision shapes — redirect them to execution instead of
            # burning strikes (one intent died to 'never carried a
            # tool_name' while the model was emitting valid plans)
            if action in ("plan_tools", "write_note", "switch_skill", "deploy_subagent"):
                messages.append({"role": "assistant", "content": response})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Good plan. Now EXECUTE it: reply with ONE JSON object "
                            '{"action": "use_tool", "tool_name": "<first tool from '
                            'your plan>", "args": {...}}'
                        ),
                    }
                )
                continue
            if action in ("ask_operator", "ask_user"):
                q = str(decision.get("question") or decision.get("reason") or "")
                return {"state": "blocked", "facts": facts, "note": f"asked operator: {q[:150]}"}
            parse_fails += 1
            if parse_fails >= 3:
                return {"state": "blocked", "facts": facts, "note": "decisions never carried a tool_name (3 strikes)"}
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": REprompt_TOOL})
            continue

        # --- execute -----------------------------------------------------
        try:
            from suijin.modules.tools.lib import dispatch

            result = dispatch.route_tool(tool_name, tool_args, config={})
            result_str = str(result)[:2000]
        except Exception as e:  # noqa: BLE001 — tool errors feed back to the LLM
            result_str = f"Error: {e}"

        if "CONFIRMED" in result_str:
            facts.append(
                {
                    "summary": f"{tool_name} confirmed: {result_str[:150]}",
                    "evidence": result_str[:200],
                }
            )

        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": f"RESULT: {result_str}"})

        if bus is not None:
            with contextlib.suppress(Exception):
                bus.publish(
                    f"/engagement/{intent.get('engagement', '?')}/work",
                    {"agent": identity, "tool": tool_name, "turn": turn, "ts": time.time()},
                )
        if on_event is not None:
            with contextlib.suppress(Exception):
                on_event(turn, tool_name, result_str)

    # hit the turn cap — exhausted (budget cut, not done)
    return {"state": "exhausted", "facts": facts, "note": f"hit {max_turns} turn cap"}
