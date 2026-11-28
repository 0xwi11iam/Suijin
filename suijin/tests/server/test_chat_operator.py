"""Chat mode: one conversation turn with the operator — the protocol, the records, the limits.

A chat session must never spawn a hunt. Its turn is the leader model answering, optionally running
Suijin tools on the way. These tests drive the turn with a scripted model and a fake tool runner, so
every path here is decided before a real provider is involved.
"""

from __future__ import annotations

import json

from suijin.server.web import chat_operator as chat


def _harness(replies, tool_result="ok", tool_error=None):
    """A turn wired to scripted model replies; returns (events, deltas, calls, seen, run)."""
    events: list[dict] = []
    deltas: list[tuple[str, str]] = []
    calls: list[tuple[str, dict]] = []
    seen: list[list[dict]] = []
    script = list(replies)

    def record(kind, **fields):
        events.append({"kind": kind, "seq": len(events) + 1, **fields})
        return len(events)

    def generate_step(messages, on_delta):
        seen.append([dict(message) for message in messages])
        reply = script.pop(0)
        if callable(reply):
            return reply(on_delta)
        return reply

    def run_tool(name, args):
        calls.append((name, dict(args or {})))
        if tool_error is not None:
            raise tool_error
        return tool_result

    def run(text="hello", **kwargs):
        return chat.run_turn(
            text,
            history=kwargs.pop("history", []),
            system_prompt="SYS",
            generate_step=generate_step,
            run_tool=run_tool,
            emit_delta=lambda kind, piece: deltas.append((kind, piece)),
            record=record,
            **kwargs,
        )

    return events, deltas, calls, seen, run


def _kinds(events):
    return [event["kind"] for event in events]


# ── protocol ────────────────────────────────────────────────────────────────────────────────


def test_a_done_step_is_the_final_answer_and_ends_the_turn():
    events, deltas, calls, seen, run = _harness(['{"thought": "easy", "reply": "All good.", "action": "done"}'])
    result = run("are you healthy?")
    assert (result["state"], result["reply"], result["steps"]) == ("done", "All good.", 1)
    assert _kinds(events) == ["assistant.reasoning", "assistant.message"]
    assert events[-1]["content"] == "All good." and events[-1]["phase"] == "final"
    # a final answer never runs a tool
    assert calls == []


def test_a_tool_step_runs_the_tool_feeds_the_result_back_and_continues():
    step = '{"thought": "look it up", "reply": "Checking the swarm…", "action": "use_tool", "tool_name": "mesh_status", "tool_args": {}}'
    final = '{"thought": "read it", "reply": "Nothing is running.", "action": "done"}'
    events, deltas, calls, seen, run = _harness([step, final], tool_result="agents: 0")
    result = run()
    assert result["state"] == "done" and result["reply"] == "Nothing is running." and result["steps"] == 2
    assert calls == [("mesh_status", {})]
    assert _kinds(events) == [
        "assistant.reasoning",
        "assistant.message",
        "tool.call",
        "tool.result",
        "assistant.reasoning",
        "assistant.message",
    ]
    assert events[1]["phase"] == "step" and events[1]["content"] == "Checking the swarm…"
    assert events[2]["name"] == "mesh_status" and events[3]["output"] == "agents: 0"
    assert events[2]["id"] == events[3]["id"]
    # the second model call sees the tool exchange in the hunt dialect the model already speaks
    second = seen[1]
    assert second[-2]["role"] == "assistant" and "use_tool" in second[-2]["content"]
    assert second[-1] == {"role": "user", "content": "RESULT: agents: 0"}


def test_prose_without_json_is_a_valid_final_answer():
    events, _, calls, _, run = _harness(["Nothing is running right now — want me to start something?"])
    result = run()
    assert result["state"] == "done"
    assert result["reply"] == "Nothing is running right now — want me to start something?"
    assert _kinds(events) == ["assistant.message"] and calls == []


def test_fenced_json_is_parsed():
    events, _, _, _, run = _harness(['Sure:\n```json\n{"reply": "Here.", "action": "done"}\n```'])
    assert run()["reply"] == "Here."


def test_the_hunt_dialect_is_understood_including_args_and_complete():
    events, _, calls, _, run = _harness(
        [
            '{"action": "use_tool", "tool_name": "read_file", "args": {"file_path": "notes.md"}, "thought": "read"}',
            '{"action": "complete", "reason": "done", "thought": "finished", "reply": "The notes say ship it."}',
        ]
    )
    result = run()
    assert calls == [("read_file", {"file_path": "notes.md"})]
    assert result["reply"] == "The notes say ship it." and result["state"] == "done"


def test_a_reply_with_no_tool_ends_the_turn_whatever_the_action_says():
    events, _, calls, _, run = _harness(['{"thought": "x", "reply": "Answer.", "action": "use_tool"}'])
    assert run()["reply"] == "Answer." and calls == []


def test_a_json_answer_that_is_not_a_decision_is_shown_verbatim():
    events, _, calls, _, run = _harness(['{"uptime_days": 3, "agents": 0}'])
    assert run()["reply"] == '{"uptime_days": 3, "agents": 0}' and calls == []


# ── reasoning ────────────────────────────────────────────────────────────────────────────────


def test_live_reasoning_is_streamed_once_and_becomes_the_record():
    def streaming(on_delta):
        on_delta("reasoning", "first ")
        on_delta("reasoning", "second")
        return '{"thought": "first second", "reply": "Done.", "action": "done"}'

    events, deltas, _, _, run = _harness([streaming])
    run()
    assert deltas == [("reasoning", "first "), ("reasoning", "second")]
    assert events[0] == {"kind": "assistant.reasoning", "seq": 1, "content": "first second"}


def test_a_thought_that_did_not_stream_is_delivered_once_by_its_record():
    """No reasoning channel on this model: the JSON thought is the reasoning, and its record
    delivers it — a delta on top would make the operator read it twice."""
    events, deltas, _, _, run = _harness(['{"thought": "let me think", "reply": "Done.", "action": "done"}'])
    run()
    assert deltas == []
    assert _kinds(events) == ["assistant.reasoning", "assistant.message"]
    assert events[0]["content"] == "let me think"


def test_no_reasoning_record_when_the_model_gave_no_thought():
    events, deltas, _, _, run = _harness(["Plain answer."])
    run()
    assert _kinds(events) == ["assistant.message"] and deltas == []


def test_reasoning_without_an_answer_is_not_mistaken_for_the_answer():
    """A reasoning model that never emits content comes back with its reasoning as the response."""

    def reasoning_only(on_delta):
        on_delta("reasoning", "I should check. ")
        on_delta("reasoning", "Let me think.")
        return "I should check. Let me think."

    events, deltas, _, seen, run = _harness(
        [reasoning_only, '{"thought": "settled", "reply": "Checked.", "action": "done"}']
    )
    result = run()
    assert result["reply"] == "Checked."
    assert "answer" in seen[1][-1]["content"].lower()
    assert [e["content"] for e in events if e["kind"] == "assistant.message"] == ["Checked."]


def test_reasoning_without_an_answer_twice_is_reported():
    def reasoning_only(on_delta):
        on_delta("reasoning", "thinking")
        return "thinking"

    events, _, _, _, run = _harness([reasoning_only, reasoning_only])
    result = run()
    assert result["state"] == "error" and "no answer" in result["note"]
    assert events[-1]["phase"] == "error"


# ── the model's view ─────────────────────────────────────────────────────────────────────────


def test_the_model_gets_system_then_history_then_the_new_message():
    history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "earlier reply"}]
    _, _, _, seen, run = _harness(["ok"])
    run("new question", history=history)
    first = seen[0]
    assert first[0] == {"role": "system", "content": "SYS"}
    assert first[1:] == history + [{"role": "user", "content": "new question"}]


def test_history_is_rebuilt_from_the_session_records():
    records = [
        {"kind": "session.start"},
        {"kind": "user.message", "content": "what is running?"},
        {"kind": "assistant.reasoning", "content": "thinking"},  # thoughts are not replayed
        {"kind": "assistant.message", "content": "checking…", "phase": "step"},
        {"kind": "tool.call", "id": "c1", "name": "mesh_status", "args": {}},
        {"kind": "tool.result", "id": "c1", "name": "mesh_status", "output": "agents: 2"},
        {"kind": "assistant.message", "content": "Two agents.", "phase": "final"},
    ]
    messages = chat.history_from_records(records)
    assert messages[0] == {"role": "user", "content": "what is running?"}
    assert messages[1]["role"] == "assistant" and "checking…" in messages[1]["content"]
    assert json.loads(messages[2]["content"])["tool_name"] == "mesh_status"
    assert messages[3] == {"role": "user", "content": "RESULT: agents: 2"}
    assert messages[4] == {"role": "assistant", "content": "Two agents."}
    assert all(m["content"] != "thinking" for m in messages)


def test_only_recent_turns_are_replayed_and_history_starts_at_a_user_message():
    records = []
    for n in range(30):
        records += [{"kind": "user.message", "content": f"q{n}"}, {"kind": "assistant.message", "content": f"a{n}"}]
    messages = chat.history_from_records(records)
    users = [m["content"] for m in messages if m["role"] == "user"]
    assert len(users) == chat.CHAT_HISTORY_TURNS
    assert users[0] == f"q{30 - chat.CHAT_HISTORY_TURNS}" and messages[0]["role"] == "user"


def test_long_tool_results_are_capped_for_the_model_but_recorded_in_full():
    long_result = "x" * (chat.CHAT_TOOL_RESULT_CAP + 500)
    _, _, _, seen, run = _harness(
        ['{"thought": "t", "reply": "r", "action": "use_tool", "tool_name": "t", "tool_args": {}}', "done"],
        tool_result=long_result,
    )
    run()
    fed = seen[1][-1]["content"]
    assert fed.startswith("RESULT: ") and len(fed) == len("RESULT: ") + chat.CHAT_TOOL_RESULT_CAP


# ── limits ──────────────────────────────────────────────────────────────────────────────────


def test_hitting_the_step_cap_forces_a_final_answer_and_runs_no_further_tool():
    always_tool = '{"thought": "t", "reply": "one more", "action": "use_tool", "tool_name": "t", "tool_args": {}}'
    events, _, calls, seen, run = _harness([always_tool] * 5)
    result = run(max_steps=2)
    assert result["state"] == "done" and result["capped"] is True
    assert len(calls) == 2
    assert _kinds(events)[-1] == "assistant.message" and events[-1]["phase"] == "final"
    # the forced answer is asked for explicitly
    assert "no more tools" in seen[-1][-1]["content"].lower()


def test_cancel_stops_the_turn_between_steps():
    always_tool = '{"thought": "t", "reply": "working", "action": "use_tool", "tool_name": "t", "tool_args": {}}'
    events, _, calls, _, run = _harness([always_tool] * 5)
    state = {"cancelled": False}
    result = run(cancelled=lambda: state["cancelled"], after_tool=lambda: state.update(cancelled=True))
    assert result["state"] == "cancelled" and len(calls) == 1
    assert "assistant.message" not in _kinds(events)[-1:]


def test_an_empty_completion_is_retried_then_reported():
    events, _, _, seen, run = _harness(["", '{"reply": "Here now.", "action": "done"}'])
    result = run()
    assert result["reply"] == "Here now." and len(seen) == 2
    assert "empty" in seen[1][-1]["content"].lower()

    events, _, _, seen, run = _harness(["", "", ""])
    result = run()
    assert result["state"] == "error" and "empty" in result["note"].lower()
    assert _kinds(events) == ["assistant.message"] and events[0]["phase"] == "error"


def test_a_provider_error_is_reported_not_shown_as_an_answer():
    events, _, _, _, run = _harness(["Error: 429 rate limited"])
    result = run()
    assert result["state"] == "error" and "429" in result["note"]
    assert events[0]["phase"] == "error" and "429" in events[0]["content"]


def test_a_message_sent_during_a_tool_step_is_read_before_the_next_step():
    script = [
        '{"thought": "t", "reply": "checking", "action": "use_tool", "tool_name": "n", "tool_args": {}}',
        '{"thought": "t", "reply": "Both answered.", "action": "done"}',
    ]
    events, _, _, seen, run = _harness(script)
    queued = ["and the findings?"]

    def take():
        # what the console does the moment the model reads a queued message: record it
        if not queued:
            return None
        text = queued.pop(0)
        events.append({"kind": "user.message", "seq": 99, "content": text})
        return text

    result = run(pending=take)
    assert result["reply"] == "Both answered."
    second_call = seen[1]
    # the result of the step, and then the operator's new message — last, where it is best heeded
    assert second_call[-2]["content"] == "RESULT: ok"
    assert "and the findings?" in second_call[-1]["content"]
    assert {"kind": "user.message", "seq": 99, "content": "and the findings?"} in events


def test_pending_is_not_consulted_before_the_first_step():
    asked = []
    _, _, _, _, run = _harness(["ok"])
    run(pending=lambda: asked.append(1))
    assert asked == []


def test_a_tool_that_raises_feeds_the_error_back_and_the_turn_continues():
    events, _, _, seen, run = _harness(
        [
            '{"thought": "t", "reply": "checking", "action": "use_tool", "tool_name": "boom", "tool_args": {}}',
            '{"thought": "t2", "reply": "That tool failed.", "action": "done"}',
        ],
        tool_error=RuntimeError("tool exploded"),
    )
    result = run()
    assert result["reply"] == "That tool failed."
    result_record = events[3]
    assert result_record["kind"] == "tool.result" and "tool exploded" in result_record["output"]
    assert "Error" in result_record["output"]
    assert "tool exploded" in seen[1][-1]["content"]


# ── prompt ──────────────────────────────────────────────────────────────────────────────────


def test_the_system_prompt_states_the_protocol_the_operator_tools_and_carries_no_hunt_doctrine():
    prompt = chat.build_system_prompt(
        tools="- **http_request**(method, url) — Make an HTTP request.",
        meta_tools=chat.OPERATOR_TOOLS,
        state_note="engagements: none",
    )
    for needle in (
        '"action"',
        "use_tool",
        "done",
        "start_engagement",
        "suijin_doctor",
        "http_request",
        "engagements: none",
    ):
        assert needle in prompt, needle
    assert "write_note" not in prompt and "MANDATORY" not in prompt
    assert chat.OPERATOR_TOOLS["start_engagement"]["args"]["mission"]["required"] is True


def test_operator_tool_schemas_are_complete():
    for name, spec in chat.OPERATOR_TOOLS.items():
        assert spec["description"].strip()
        for arg, arg_spec in spec["args"].items():
            assert arg_spec["type"] and arg_spec["description"], (name, arg)
    assert set(chat.OPERATOR_TOOLS) == {"start_engagement", "suijin_doctor"}
    assert chat.OPERATOR_TOOLS["suijin_doctor"]["args"] == {}
