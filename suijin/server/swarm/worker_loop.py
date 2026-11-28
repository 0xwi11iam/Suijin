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
import os
import re

SYSTEM_TEMPLATE = (
    "You are a worker agent on an authorized bug bounty engagement. "
    "Your ONLY task is the intent below. Work it to completion using the "
    "available tools — really run them, do not describe what you would run.\n\n"
    "{catalog}\n\n"
    "TARGETS ARE INEXHAUSTIBLE — the operator's standing doctrine:\n"
    "- A target is NEVER fully tested. There is always another endpoint, "
    "parameter, auth role, HTTP method, header quirk, or edge case. "
    "\"The target is done\" is not an available answer — ever.\n"
    "- \"complete\" means THIS narrow intent is finished with real tool "
    "evidence — never that the target is finished.\n"
    "- When the operator says keep going / get back on / continue, that is "
    "a STANDING ORDER: replying \"complete\" is forbidden. Pick the next "
    "untested endpoint, parameter, or role and FIRE A TOOL on it.\n"
    "- When unsure what is next: enumerate first (endpoint discovery, "
    "param mining, role matrix), then work the list top-down. Enumeration "
    "is progress; judging the target \"done\" is not.\n\n"
    "Every reply must be ONE JSON object:\n"
    '  {{"action": "use_tool", "tool_name": "<name from the catalog>", '
    '"args": {{"...": "..."}}, "thought": "..."}}\n'
    'or when THIS intent is fully worked (real tool evidence, intent-scope '
    'only — never "the target is done"):\n'
    '  {{"action": "complete", "reason": "...", "facts": [...]}}\n'
    "Escape EVERY double quote inside a value as \\\" — a bare quote breaks the object and "
    "your reply is rejected. Large tool payloads (over ~20 KB) go to a file first "
    "(write_file), then the call references the path — never inline them.\n"
    "Prose-only replies are errors and will be rejected. Premature "
    '"complete" replies are rejected and returned to you with the next '
    "untested surface named — fire the tool."
)


def _dead_regions_hint(engagement: str = "_default") -> str:
    """Recent DEAD REGIONS from peers — riding every worker's prompt so
    the swarm never retraces a corpse (operator directive 2026-11-06)."""
    with contextlib.suppress(Exception):
        from suijin.server.tools.lib.dead_regions import ledger_rows

        rows = ledger_rows(engagement=engagement)
        fresh = [r for r in rows if r["verdict"].startswith("fresh")][-6:]
        stale = [r for r in rows if r["verdict"].startswith("stale")][-3:]
        if not fresh and not stale:
            return ""
        parts = ["\n\nTESTED LEDGER (peers' verdicts, with age — sites change):"]
        if fresh:
            listed = "; ".join(f"{r['region'][:40]} ({int(r['age_days'])}d ago)" for r in fresh)
            parts.append(f"  do NOT retest (fresh): {listed}")
        if stale:
            listed = "; ".join(f"{r['region'][:40]} ({int(r['age_days'])}d ago)" for r in stale)
            parts.append(f"  STALE verdicts — the site may have changed, consider retesting: {listed}")
        parts.append("  add your own via mark_dead(region, reason).")
        return "\n".join(parts)
    return ""


def _hunt_policy() -> str:
    """The operator's standing hunt policy — ~/.suijin/config/hunt-
    policy.md rides EVERY agent's instructions by default (no per-
    engagement repeating: 'I have to tell the agents myself' — never
    again). Boxed agents see the same file through the mounted config
    dir. Missing/empty file → only the built-in defaults below."""
    builtin = (
        "\n\nSTANDING HUNT POLICY (defaults):\n"
        "- impact over noise — pursue findings the program would PAY for; "
        "cosmetic/unpayable findings get one note, then move on\n"
        "- drop dead surface fast — no real attack surface? one note, leave\n"
        "- NO iteration budget in swarm mode — work until the mission is "
        "genuinely done; do not invent caps\n"
        "- TARGETS ARE INEXHAUSTIBLE — never report a target fully tested; "
        "there is always another endpoint, parameter, role, or method. "
        "\"complete\" is scoped to THIS intent's evidence only\n"
        "- operator continuation orders (keep going / get back on) are "
        "standing orders — replying complete after one is forbidden; fire "
        "the next untested surface instead\n"
    )
    with contextlib.suppress(Exception):
        from pathlib import Path as _P

        from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

        f = _P(WORKSPACE_DIR) / "hunt-policy.md"
        if f.exists():
            custom = f.read_text(errors="replace").strip()
            if custom:
                return "\n\nSTANDING HUNT POLICY (operator):\n" + custom[:4000]
    return builtin


def _session_hint() -> str:
    """One line telling workers which authenticated sessions exist.

    The operator logs in via `suijin auth` (or the live jar); agents
    never see values — names only, and the model passes the name to
    http_request/http_replay via credential='<name>'. Without this
    hint the sessions sat unused because no prompt mentioned them."""
    with contextlib.suppress(Exception):
        from suijin.modules.credential_store.main import _load_store

        store = _load_store() or {}
        entries = list(store.get("credentials", [])) if isinstance(store, dict) else []

        import re as _re

        cf_only = re.compile(
            r"^(__cf_bm|__cfuvid|__cfruid|cf_clearance|__cf_chl\S*|__cfhostassets)$", _re.I
        )

        def _split(value: str) -> list[str]:
            return [p.split("=", 1)[0].strip() for p in str(value or "").replace("; ", ";").split(";") if p.strip()]

        logged_in, bot_only = [], []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("service") or entry.get("name") or "")
            cookies = _split(entry.get("value", ""))
            if any(not cf_only.match(c) for c in cookies):
                logged_in.append(name)
            elif cookies:
                bot_only.append(name)

        parts = []
        if logged_in:
            shown = ", ".join(sorted(set(logged_in))[:8])
            parts.append(
                f"\n\nAUTHENTICATED SESSIONS — logged-in jars on file: {shown}\n"
                "AUTHENTICATED BY DEFAULT (operator standing rule): for ANY target "
                "domain in that list, test AS the logged-in user — pass "
                "credential='<domain>' to http_request / http_replay / auth_diff. "
                "Authenticated surface is the priority; unauthenticated probing is "
                "for comparison (auth_diff) or when no jar exists for the domain. "
                "Never probe a jarred domain blind."
            )
        if bot_only:
            shown = ", ".join(sorted(set(bot_only))[:8])
            parts.append(
                f"\n\nJARS WITHOUT A LOGIN (bot cookies only — do NOT treat these "
                f"as authenticated): {shown}. Cloudflare sets __cf_bm/__cfuvid on "
                "every site you touch; they are bot-allowlist cookies, not a "
                "session. To get a real session, fire request_auth."
            )
        mechanics = (
            "\n\nHOW COOKIES WORK FOR YOU (mechanics): every http_request you make "
            "automatically sends the jar's cookies for that domain — you never "
            "read, set, or paste cookie values yourself. Cloudflare bot cookies "
            "ride along on every request and are normal, not a login. When the "
            "operator completes a request_auth, the session lands IN YOUR JAR — "
            "your next request to that domain is simply authenticated. Verify a "
            "session by requesting an endpoint that returns personal data: 200 "
            "+ user data = live session; a guest page or 401 = still anonymous."
        )
        parts.append(mechanics)
        return "\n".join(parts)
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


#: a failure no retry can fix: the model id, the key or the account is wrong. Retrying it nine
#: times behind the operator's back is how an engagement "thought" for nine minutes (2026-11-28).
_PERMANENT_FAILURE = re.compile(
    r"\b(?:400|401|403|404)\b"
    r"|unknown model|invalid api key|incorrect api key|unauthori[sz]ed|forbidden"
    r"|must be author/model|no such model|does not exist|no api key|missing api key",
    re.I,
)


def is_permanent_failure(note: str) -> bool:
    """True when a blocked note describes a failure the OPERATOR must fix (model, key, account):
    retrying cannot help, so the agent says so once and waits."""
    return bool(_PERMANENT_FAILURE.search(str(note or "")))


def _failure_note(note: str, route: str, doubt: str) -> str:
    """What the operator reads when the model call failed: the error, who was called, and — when
    the route itself looks wrong — why."""
    text = " ".join(str(note or "").split())[:400]
    if route:
        text = f"{text} [called {route}]"
    return f"{text} — {doubt}" if doubt else text


def _reasoning_tap(turn: int, on_reasoning):
    """``on_delta`` for generate(): the model's reasoning, handed to ``on_reasoning`` in pieces as
    it streams. The reply's own tokens are ignored — the decision is parsed once, whole, after the
    call. None when nobody is listening, so generate() runs exactly as it did before."""
    if on_reasoning is None:
        return None
    pending: list[str] = []

    def flush() -> None:
        text = "".join(pending).strip()
        pending.clear()
        if text:
            with contextlib.suppress(Exception):  # showing reasoning must never break the call
                on_reasoning(turn, text)

    def on_delta(kind, text) -> None:
        if kind == "reasoning" and text:
            pending.append(str(text))
            if sum(len(piece) for piece in pending) >= 240:
                flush()

    on_delta.flush = flush
    return on_delta


def execute_intent(
    identity: str,
    intent: dict,
    bus=None,
    max_turns: int = 0,
    on_event=None,
    on_turn=None,
    on_decision=None,
    on_reasoning=None,
) -> dict:
    """Work one intent. The result carries ``model`` — "provider:model" of the last call — when
    a call was made, so the console can show what an agent thinks with."""
    route: dict = {}
    result = _work_intent(
        identity,
        intent,
        bus=bus,
        max_turns=max_turns,
        on_event=on_event,
        on_turn=on_turn,
        on_decision=on_decision,
        on_reasoning=on_reasoning,
        _route=route,
    )
    if route.get("route"):
        result["model"] = route["route"]
    return result


def _work_intent(
    identity: str,
    intent: dict,
    bus=None,
    max_turns: int = 0,
    on_event=None,
    on_turn=None,
    on_decision=None,
    on_reasoning=None,
    _route: dict | None = None,
) -> dict:
    """Work one intent: LLM → decision → tool → result → repeat.

    Returns {"state": done|blocked|exhausted, "facts": [...], "note": str}.

    on_turn(turn) → optional str: called at the TOP of every turn; a
    non-None return is injected into the conversation as an OPERATOR
    INTERJECTION user message (this is how `suijin swarm say` reaches
    agents mid-intent instead of just printing to their log).

    What the operator sees, in the order it happens (2026-11-28 — an agent
    used to report a turn only once its tool had returned, so a turn that
    died on the model call reported nothing at all):
      on_reasoning(turn, text)  the model's own reasoning, in pieces, as it streams
      on_decision(turn, thought, tool_name, tool_args)  the moment the decision is
                                parsed and BEFORE the tool runs
      on_event(turn, tool_name, result, args, thought=)  the tool's result

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
        {"role": "system",
         "content": (SYSTEM_TEMPLATE.format(catalog=_load_catalog())
                     + _session_hint() + _hunt_policy()
                     + "\n\nLANGUAGE (operator standing rule): write every note, finding, "
                       "report, and response in ENGLISH — regardless of the language "
                       "the target site renders in. Translate anything you read "
                       "before storing or broadcasting it."
                     + _dead_regions_hint(intent.get("engagement") or "_default"))},
        {"role": "user", "content": f"INTENT: {intent['text']}\n\nExecute this intent."},
    ]

    facts: list[dict] = []
    parse_fails = 0
    tool_calls_made = 0      # real dispatches this intent (never-done gate)
    complete_rejections = 0  # premature completes rejected by the gate
    recent_results: list[str] = []  # fixation guard input
    fixated: set[str] = set()       # already-corrected regions
    escalate = False  # tiered routing: CONFIRMED/long results → planner model on the next call

    def _fixation_check() -> str | None:
        """THE FIXATION GUARD: if 4+ of the last 6 results touch a region
        the ledger marks dead-fresh, the worker corrects ITSELF — the
        open-redirect fixation class (operator: 'agent keeps pushing on
        it when it's not the most efficient direction')."""
        if len(recent_results) < 6:
            return None
        with contextlib.suppress(Exception):
            from suijin.server.tools.lib.dead_regions import ledger_rows

            for row in ledger_rows(engagement=intent.get("engagement") or "_default"):
                if not row["verdict"].startswith("fresh"):
                    continue
                key = row["region"][:40]
                if key in fixated:
                    continue
                hits = sum(1 for r in recent_results[-6:] if key.lower()[:24] in r.lower())
                if hits >= 4:
                    fixated.add(key)
                    return (
                        f"you have probed '{row['region'][:60]}' {hits}/6 recent turns — the "
                        f"tested ledger marks it DEAD ({row['reason'][:50]}). DROP IT and move "
                        "to a different surface; retesting it wastes the engagement."
                    )
        return None

    for turn in range(max_turns):
        # OPERATOR STOP (2026-11-15): the console's stop sets agents.status
        # = 'stopped' — honor it at every turn boundary. Process agents get
        # /cmd/**; in-process (inline) agents only see the db.
        if turn % 1 == 0:
            with contextlib.suppress(Exception):
                import sqlite3 as _sq3
                from suijin.server.swarm.coordinator import swarm_db_path

                _stop_db = _sq3.connect(str(swarm_db_path()), timeout=2)
                _row = _stop_db.execute(
                    "SELECT status FROM agents WHERE id = ?", (identity,)
                ).fetchone()
                _stop_db.close()
                if _row and str(_row[0] or "").lower() == "stopped":
                    return {"state": "stopped", "facts": facts,
                            "note": "stopped by operator"}
        # operator interjection (swarm say) — lands mid-conversation
        if on_turn is not None:
            with contextlib.suppress(Exception):
                interjection = on_turn(turn) or _fixation_check()
                if interjection:
                    messages.append(
                        {
                            "role": "user",
                            "content": f"OPERATOR INTERJECTION: {interjection} — incorporate this into your current work.",
                        }
                    )

        # --- model call -------------------------------------------------
        # Which provider and model this call goes to, and whether the route itself looks wrong.
        # A failing call says both: "Unknown Model" from Z.ai meant nothing until it read
        # "[called zai:deepseek/…]" (2026-11-28).
        route = ""
        doubt = ""
        try:
            from suijin.modules.platform.lib.config_loader import load_config, load_env

            load_env()  # pick up the operator's .env (API keys)
            from suijin.modules.providers.lib import generate

            cfg = load_config()
            # TIERED ROUTING (fully wired): volume work → the cheap
            # model; CONFIRMED results or long observations escalate the
            # next call to the frontier model (finding analysis deserves
            # the planner brain). No config = unchanged.
            with contextlib.suppress(Exception):
                from suijin.server.tools.lib.model_routing import (
                    apply_routing,
                    routing_doubt,
                    routing_note,
                    should_escalate,
                )

                tier_env = os.environ.get("SUIJIN_TIER")
                if tier_env:
                    # structured mode: this agent is a leader/supervisor/
                    # worker — the tier cascade picks its model
                    tier = f"tier:{tier_env}"
                else:
                    tier = "planner" if escalate else "worker"
                doubt = routing_doubt(cfg, tier)
                routed_model, routed_provider = routing_note(cfg, tier)
                route = f"{routed_provider}:{routed_model}" if routed_provider else routed_model
                if _route is not None:
                    _route["route"] = route
                apply_routing(cfg, task=tier)
            cfg["max_tokens_per_request"] = max(
                int(cfg.get("max_tokens_per_request") or 0), 8000
            )  # long shell payloads truncated mid-JSON = the dominant
            # unparseable-reply cause; 4k cut real probes off
            cfg["intelligence"] = "medium"  # workers don't need max thinking
            cfg["response_format_json"] = True  # proven accepted by z.ai
            # (2026-11-06 probe) — worker replies are always ONE JSON
            # decision; enforcement kills the strike class at the source
            tap = _reasoning_tap(turn, on_reasoning)
            try:
                response = generate(messages, cfg, on_delta=tap)
            finally:
                if tap is not None:
                    tap.flush()  # the reasoning that was still buffered when the call returned
        except Exception as e:  # noqa: BLE001 — provider errors become 'blocked'
            return {"state": "blocked", "facts": facts, "note": _failure_note(f"provider error: {e}", route, doubt)}

        if not isinstance(response, str) or response.startswith("Error:"):
            return {"state": "blocked", "facts": facts, "note": _failure_note(str(response), route, doubt)}

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
            strike_limit = int(cfg.get("model_strike_limit") or 5)
            if parse_fails >= strike_limit:
                return {"state": "blocked", "facts": facts, "note": f"model never emitted a JSON decision ({strike_limit} strikes)"}
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": REprompt_TRUNCATED if truncated else REprompt_TOOL})
            continue

        action = str(decision.get("action") or "use_tool")

        if action == "complete":
            reason = str(decision.get("reason") or decision.get("completion_reason") or "done")
            # NEVER-DONE GATE (2026-11-21): the model "feels done", claims the
            # target is finished, and idles while the operator screams "get
            # back on". A target is inexhaustible — "done" about the target is
            # a lie. Gate it structurally:
            #   < 3 real tool calls on this intent → rejected (zero evidence)
            #   any rejection → re-prompted with the next untested surface
            #   3 rejections → honored, but flagged "forced" for the coordinator
            # ZERO-evidence completion: reject hard, up to 6 strikes, then
            # blocked (coordinator re-queues) — never a false "done".
            if tool_calls_made == 0:
                complete_rejections += 1
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": (
                    "REJECTED — this intent has ZERO real tool calls behind "
                    + "it. A target is never fully tested and you have not "
                    + "worked anything. Pick the next untested endpoint, "
                    + "parameter, or auth role in scope and FIRE A TOOL on "
                    + "it now. Reply with {\"action\": \"use_tool\", ...}"
                )})
                if complete_rejections >= 6:
                    return {"state": "blocked", "facts": facts,
                            "note": f"refused to work: {complete_rejections} rejected completes with zero tool calls"}
                continue
            # Evidenced completion: accept, but pressure it with the
            # never-done doctrine first — the operator's loop-killer.
            complete_rejections += 1
            if complete_rejections <= 3:
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": (
                    "REJECTED — a target is never fully tested (inexhaustible "
                    "by nature: always another endpoint, parameter, role, or "
                    "method). If THIS intent is genuinely exhausted with "
                    "evidence above, the next untested surface is already in "
                    "your scope: enumerate it and FIRE A TOOL. Only a finding "
                    "that requires stopping the whole engagement justifies "
                    "stopping now — otherwise your next reply is a tool call."
                )})
                continue
            return {"state": "done", "facts": facts,
                    "note": f"{reason[:140]} (accepted after {complete_rejections - 1} rejected completes — target NOT exhausted)"}

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

        # The decision is in: show it BEFORE the tool runs. A probe that takes minutes is work in
        # progress, not silence (2026-11-28).
        if on_decision is not None:
            with contextlib.suppress(Exception):
                on_decision(
                    turn,
                    str(decision.get("thought") or decision.get("reasoning") or ""),
                    tool_name,
                    tool_args,
                    model=route,
                )
        # --- execute -----------------------------------------------------
        try:
            from suijin.modules.tools.lib import dispatch

            result = dispatch.route_tool(tool_name, tool_args, config={})
            result_str = str(result)[:2000]
        except Exception as e:  # noqa: BLE001 — tool errors feed back to the LLM
            result_str = f"Error: {e}"

        tool_calls_made += 1
        complete_rejections = 0   # real tool evidence — gate trusts progress again
        recent_results.append(result_str[:300])
        with contextlib.suppress(Exception):
            from suijin.server.tools.lib.model_routing import should_escalate

            escalate = should_escalate(len(result_str), has_confirmed="CONFIRMED" in result_str)
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
                on_event(turn, tool_name, result_str, tool_args,
                         thought=str(decision.get("thought") or decision.get("reasoning") or ""))

    # hit the turn cap — exhausted (budget cut, not done)
    return {"state": "exhausted", "facts": facts, "note": f"hit {max_turns} turn cap"}
