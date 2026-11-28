"""Chat mode — the operator talks to Suijin; Suijin answers, and acts when asked.

An *engagement* session spawns a hunt. A *chat* session runs a conversation turn: the leader
model reads the thread, thinks out loud, and may run any Suijin tool — or the two operator
tools, ``start_engagement`` and ``suijin_doctor`` — before it answers.

The turn speaks the JSON decision the hunt workers already use, so the operator's models follow
it without a new dialect::

    {"thought": "...", "reply": "...", "action": "use_tool" | "done",
     "tool_name": "...", "tool_args": {...}}

``reply`` is what the operator reads. A step that names a tool runs it, feeds the ``RESULT```
back and continues; ``done`` — or any reply without a tool — ends the turn. Plain prose with no
JSON is a valid final answer: chat is a conversation, not a form.

Everything that touches the outside is injected (``generate_step``, ``run_tool``, ``record``,
``emit_delta``), so a turn is decided by these functions and tested without a provider:

* ``record(kind, **fields)`` appends one record to the session's ``events.jsonl`` and returns its seq
* ``emit_delta("reasoning", text)`` streams one piece of live reasoning to the session's event ring

Record kinds written: ``user.message`` (by the caller), ``assistant.reasoning``, ``assistant.message``
(``phase`` = ``step`` | ``final`` | ``error``), ``tool.call``, ``tool.result``.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from typing import Callable

#: Model steps in one turn (a step is one reply, with or without a tool).
CHAT_MAX_STEPS = 12
#: Characters of one tool result fed back to the model (the record keeps more).
CHAT_TOOL_RESULT_CAP = 6000
#: Characters of one tool result kept in the session record.
CHAT_RECORD_CAP = 20000
#: Earlier exchanges replayed for the model.
CHAT_HISTORY_TURNS = 12
#: Consecutive empty completions retried before the turn reports an error (free models do this).
CHAT_EMPTY_RETRIES = 2

#: The operator's two tools. Not registered with the hunt agents — only a chat turn can call them.
OPERATOR_TOOLS: dict[str, dict] = {
    "start_engagement": {
        "description": (
            "Start a new engagement — an autonomous hunt in its own session. Use it only when the "
            "operator asks for an engagement. Returns the session id and the path to open it."
        ),
        "args": {
            "mission": {
                "type": "string",
                "required": True,
                "description": "What the engagement should do, in one or two clear sentences.",
            },
            "target": {
                "type": "string",
                "required": False,
                "description": "The in-scope host(s) or program, when the operator named one.",
            },
            "permissions": {
                "type": "string",
                "required": False,
                "description": "recon (read-only) | exploitation (safe-harbour) | maximum. Default: exploitation.",
            },
            "mode": {"type": "string", "required": False, "description": "bbp | vdp | ctf | pentest. Default: vdp."},
        },
    },
    "suijin_doctor": {
        "description": "Check that Suijin is healthy right now — model connection, swarm, knowledge "
        "graph, config — and return what is wrong, if anything.",
        "args": {},
    },
}

_SYSTEM_PROMPT = """You are Suijin — the operator's security automation console — talking to the operator in a chat.

This is a CHAT, not a hunt. The operator asks; you answer, and you act when they ask you to.
Never start probing, scanning or exploiting a target on your own. A hunt starts only through
start_engagement, and only when the operator asks for one.

HOW TO REPLY — every step is ONE JSON object and nothing else:
{"thought": "<your reasoning, plain sentences>", "reply": "<what the operator reads, GitHub-flavoured Markdown>", "action": "use_tool" | "done", "tool_name": "<tool>", "tool_args": {...}}
- reply + "done"            -> your final answer. The turn ends.
- reply + "use_tool"        -> reply is one short line about what you are doing ("Checking the swarm…");
                               the tool runs, you get a RESULT, and you continue.
- tool_name and tool_args are left out on "done".
- The JSON must be valid: escape quotes and newlines, no trailing commas, no comments.

TOOLS: whenever the answer depends on this machine's real state — engagements, agents, findings,
skills, files, memory, config — look it up instead of guessing. Do not call a tool that is not
listed. For anything that changes state (starting an engagement, writing files, running
commands), do it only when the operator asked for that specific thing, and say exactly what you did.

STARTING AN ENGAGEMENT: call start_engagement with a clear mission. Its RESULT gives a path —
put that path in your reply as a Markdown link labelled "Open engagement" so the operator can
jump straight to it. One engagement per request unless told otherwise.

ANSWERING: lead with the answer, then the detail the operator needs. No filler, no flattery, no
restating the question; short questions get short answers. When you don't know, say so plainly.
Never invent findings, results, targets or numbers.
{tools}
{state}"""


def build_system_prompt(tools: str, meta_tools: dict | None = None, state_note: str = "") -> str:
    """The chat prompt: behaviour, protocol, the operator tools, the tool list, the live state.

    ``tools`` is the plain one-line-per-tool listing — never the hunt agents' catalog, whose
    header orders a note after every action and a knowledge query before every payload."""
    meta = meta_tools if meta_tools is not None else OPERATOR_TOOLS
    lines = ["\nOPERATOR TOOLS (yours alone):"]
    for name, spec in meta.items():
        args = ", ".join(arg + ("" if info.get("required") else "?") for arg, info in spec["args"].items())
        lines.append(f"- {name}({args}) — {spec['description']}")
        for arg, info in spec["args"].items():
            lines.append(f"    {arg}: {info['description']}")
    listing = "\n\nAVAILABLE TOOLS (call by tool_name exactly as written):\n" + (tools or "(none)").strip()
    state = (
        "\n\nSTATE NOW (refreshed every turn; look anything else up):\n"
        + (state_note or "(unknown — look it up)").strip()
    )
    # replace(), not format(): the prompt shows the protocol's literal JSON braces.
    return _SYSTEM_PROMPT.replace("{tools}", "\n".join(lines) + listing).replace("{state}", state)


# ── parsing ─────────────────────────────────────────────────────────────────────────────────

_DECISION_KEYS = ("action", "tool_name", "tool", "reply", "thought")


def _balanced_objects(text: str) -> list[str]:
    """Every top-level {...} block, brace-balanced and string-aware (the hunt loop's own)."""
    with contextlib.suppress(Exception):
        from suijin.server.swarm.worker_loop import _balanced_objects as upstream

        return upstream(text)
    objects: list[str] = []
    depth, start, in_str, esc = 0, None, False, False
    for index, char in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif char == "\\":
                esc = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(text[start : index + 1])
                start = None
    return objects


def _loads(text: str) -> dict | None:
    with contextlib.suppress(Exception):
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    return None


def _decision_object(text: str) -> dict | None:
    """The model's decision: a fenced ```json block wins, else the LAST balanced object that
    parses AND looks like a decision. None when the reply carries no decision — then it is prose."""
    fenced: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            inside = not inside
            if inside:
                fenced.append("")
            continue
        if inside and fenced:
            fenced[-1] += line + "\n"
    for block in fenced:
        obj = _loads(block.strip())
        if obj and any(key in obj for key in _DECISION_KEYS):
            return obj
    for raw in reversed(_balanced_objects(text)):
        obj = _loads(raw)
        if obj and any(key in obj for key in _DECISION_KEYS):
            return obj
    return None


def _mapping(*values) -> dict:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def parse_step(response: str) -> dict:
    """One model reply → {thought, reply, action, tool_name, tool_args}."""
    text = str(response or "").strip()
    step = {"thought": "", "reply": text, "action": "done", "tool_name": "", "tool_args": {}}
    obj = _decision_object(text)
    if obj is None:
        return step
    thought = str(obj.get("thought") or obj.get("reasoning") or "").strip()
    reply = str(obj.get("reply") or obj.get("message") or obj.get("answer") or obj.get("content") or "").strip()
    tool = str(obj.get("tool_name") or obj.get("tool") or "").strip()
    action = str(obj.get("action") or "").strip().lower()
    if action not in ("use_tool", "use tool", "tool", "call_tool", "run_tool"):
        action = "done"
    return {
        "thought": thought,
        "reply": reply,
        "action": action if tool else "done",
        "tool_name": tool,
        "tool_args": _mapping(obj.get("tool_args"), obj.get("args"), obj.get("arguments")),
    }


# ── the model's view of the thread ──────────────────────────────────────────────────────────


def history_from_records(records: list[dict], max_turns: int = CHAT_HISTORY_TURNS) -> list[dict]:
    """Earlier turns as model messages, newest ``max_turns`` exchanges, starting at a user message.

    Thoughts are not replayed — they were reasoning, not the conversation."""
    starts = [index for index, record in enumerate(records) if record.get("kind") == "user.message"]
    if not starts:
        return []
    messages: list[dict] = []
    for record in records[starts[max(0, len(starts) - max_turns)] :]:
        kind = record.get("kind")
        if kind == "user.message":
            messages.append({"role": "user", "content": str(record.get("content") or "")})
        elif kind == "assistant.message":
            content = str(record.get("content") or "")
            if content:
                messages.append({"role": "assistant", "content": content})
        elif kind == "tool.call":
            messages.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "action": "use_tool",
                            "tool_name": str(record.get("name") or "tool"),
                            "tool_args": record.get("args") or {},
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        elif kind == "tool.result":
            messages.append(
                {"role": "user", "content": "RESULT: " + str(record.get("output") or "")[:CHAT_TOOL_RESULT_CAP]}
            )
    return messages


# ── one turn ────────────────────────────────────────────────────────────────────────────────

_STEP_CAP_NOTE = (
    "I hit this turn's tool limit before I could finish. Say \"continue\" and I'll pick up where I stopped."
)


def _response_problem(response) -> str | None:
    if not isinstance(response, str):
        return f"the model returned {type(response).__name__} instead of text"
    text = response.strip()
    if not text or text == "(empty response)":
        return "empty"
    if text.startswith("Error:"):
        return f"the model returned an error ({text[:200]})"
    return None


def _model_step(generate_step: Callable, messages: list[dict], emit_delta: Callable) -> tuple[object, str]:
    """One model call. Live reasoning pieces are streamed to the operator as they arrive."""
    pieces: list[str] = []

    def on_delta(kind, text):
        if kind == "reasoning" and text:
            pieces.append(str(text))
            with contextlib.suppress(Exception):
                emit_delta("reasoning", str(text))

    try:
        response = generate_step(messages, on_delta)
    except Exception as error:  # noqa: BLE001 — a provider failure is the turn's error, never a crash
        response = f"Error: {type(error).__name__}: {error}"
    return response, "".join(pieces).strip()


def _record_thought(thought: str, streamed: str, record: Callable) -> None:
    """The reasoning the operator sees is what streamed live, else the JSON thought — delivered
    ONCE, by its record: the record is rung the moment it is written, so a thought that did not
    stream must not also be sent as a delta or the operator would read it twice."""
    content = streamed or str(thought or "").strip()
    if content:
        record("assistant.reasoning", content=content)


def run_turn(
    user_text: str,
    *,
    history: list[dict],
    system_prompt: str,
    generate_step: Callable,
    run_tool: Callable,
    emit_delta: Callable,
    record: Callable,
    cancelled: Callable[[], bool] | None = None,
    after_tool: Callable[[], None] | None = None,
    pending: Callable[[], str | None] | None = None,
    max_steps: int = CHAT_MAX_STEPS,
) -> dict:
    """Run one conversation turn for ``user_text``.

    ``pending()`` is asked before every step after the first: when it returns text, the operator
    sent a message while the turn was working, and the model reads it before going on.

    Returns ``{state, reply, steps, note, capped}`` with state ``done`` | ``cancelled`` | ``error``.
    """
    cancelled = cancelled or (lambda: False)
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "user", "content": user_text},
    ]
    steps = 0
    empties = 0
    nudges = 0
    for _ in range(max_steps):
        if cancelled():
            return {"state": "cancelled", "reply": "", "steps": steps, "note": "cancelled", "capped": False}
        if steps and pending is not None:
            extra = pending()
            if extra:
                messages.append({"role": "user", "content": f"The operator sent this while you were working: {extra}"})
        response, streamed = _model_step(generate_step, messages, emit_delta)
        problem = _response_problem(response)
        # A reasoning model that never emits content hands its reasoning back AS the response
        # (see providers generate): that is a missing answer, not one — and it already streamed.
        if problem is None and streamed and isinstance(response, str) and response.strip() == streamed:
            problem = "reasoning-only"
        if problem == "reasoning-only" and nudges < 1:
            nudges += 1
            messages += [
                {"role": "assistant", "content": response},
                {
                    "role": "user",
                    "content": "That was only your reasoning. Now answer the operator: "
                    "reply with the JSON object (thought, reply, action).",
                },
            ]
            continue
        if problem == "reasoning-only":
            problem = "the model returned reasoning but no answer"
        if problem == "empty" and empties < CHAT_EMPTY_RETRIES:
            empties += 1
            messages += [
                {"role": "assistant", "content": ""},
                {"role": "user", "content": "Your last reply was empty. Answer the operator now."},
            ]
            continue
        if problem:
            note = (
                f"the model returned an empty reply {CHAT_EMPTY_RETRIES + 1} times" if problem == "empty" else problem
            )
            record("assistant.message", content=f"I couldn't answer that one: {note}. Send it again.", phase="error")
            return {"state": "error", "reply": "", "steps": steps, "note": note, "capped": False}
        empties = 0
        step = parse_step(response)
        steps += 1
        _record_thought(step["thought"], streamed, record)
        use_tool = step["action"] == "use_tool" and bool(step["tool_name"])
        if step["reply"]:
            record("assistant.message", content=step["reply"], phase="step" if use_tool else "final")
        if not use_tool:
            return {
                "state": "done",
                "reply": step["reply"] or step["thought"] or _STEP_CAP_NOTE,
                "steps": steps,
                "note": "",
                "capped": False,
            }
        call_id = f"chat-{steps}-{uuid.uuid4().hex[:8]}"
        record("tool.call", id=call_id, name=step["tool_name"], args=step["tool_args"])
        try:
            result = run_tool(step["tool_name"], step["tool_args"])
            result_text = result if isinstance(result, str) else json.dumps(result, default=str, ensure_ascii=False)
        except Exception as error:  # noqa: BLE001 — a tool error feeds back to the model
            result_text = f"Error: {type(error).__name__}: {error}"
        record("tool.result", id=call_id, name=step["tool_name"], output=result_text[:CHAT_RECORD_CAP])
        if after_tool is not None:
            after_tool()
        messages += [
            {"role": "assistant", "content": response},
            {"role": "user", "content": f"RESULT: {result_text[:CHAT_TOOL_RESULT_CAP]}"},
        ]

    # Step cap: one more call for a wrap-up, and whatever it says is the final answer.
    if cancelled():
        return {"state": "cancelled", "reply": "", "steps": steps, "note": "cancelled", "capped": True}
    messages.append(
        {
            "role": "user",
            "content": (
                f"STEP LIMIT: you have used all {max_steps} tool steps for this turn. Answer the operator now "
                'with your best reply so far — action "done", no more tools.'
            ),
        }
    )
    response, streamed = _model_step(generate_step, messages, emit_delta)
    note = f"hit the {max_steps}-step tool limit"
    if _response_problem(response):
        record("assistant.message", content=_STEP_CAP_NOTE, phase="final")
        return {"state": "done", "reply": _STEP_CAP_NOTE, "steps": steps, "note": note, "capped": True}
    step = parse_step(response)
    steps += 1
    _record_thought(step["thought"], streamed, record)
    reply = step["reply"] or step["thought"] or _STEP_CAP_NOTE
    record("assistant.message", content=reply, phase="final")
    return {"state": "done", "reply": reply, "steps": steps, "note": note, "capped": True}
