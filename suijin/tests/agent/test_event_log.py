"""Event log — append-only engagement records; replay reconstructs state.

Contract:
  - append(): known kinds only, atomic lines, seq monotonic, never raises
  - a torn tail line (crash mid-write) is dropped by read_events
  - replay(): the .sje resume contract from the log — objective, phase,
    iteration, messages (assistant + guidance), execution trace with
    success/error_kind, trimmed to the .sje caps
"""

import json

import pytest

from suijin.modules.agent.lib.event_log import EventLog, read_events, replay, tool_output_cap


@pytest.fixture()
def log(tmp_path):
    el = EventLog(tmp_path / "events.jsonl")
    yield el
    el.close()


class TestAppend:
    def test_records_written_with_seq_and_version(self, tmp_path):
        el = EventLog(tmp_path / "events.jsonl")
        a = el.append("session.start", objective="obj")
        b = el.append("iteration", n=1, phase="informational")
        el.close()
        assert a == 1 and b == 2
        recs = read_events(tmp_path / "events.jsonl")
        assert recs[0]["v"] == 1 and recs[0]["seq"] == 1 and recs[0]["kind"] == "session.start"
        assert recs[1]["kind"] == "iteration" and recs[1]["n"] == 1

    def test_unknown_kind_refused(self, log):
        assert log.append("not.a.kind") == 0
        assert log.append("session.start", objective="x") == 1  # seq not burned

    def test_closed_log_silent(self, tmp_path):
        el = EventLog(tmp_path / "e.jsonl")
        el.close()
        assert el.append("iteration", n=1) == 0  # never raises

    def test_torn_tail_dropped(self, tmp_path):
        p = tmp_path / "e.jsonl"
        p.write_text(json.dumps({"v": 1, "seq": 1, "kind": "session.start"}) + "\n" + '{"v":1,"seq":2,"kind":"itera')
        recs = read_events(p)
        assert len(recs) == 1 and recs[0]["kind"] == "session.start"

    def test_seq_continues_on_reopen(self, tmp_path):
        p = tmp_path / "e.jsonl"
        el = EventLog(p)
        el.append("session.start", objective="x")
        el.close()
        el2 = EventLog(p)
        assert el2.append("iteration", n=1) == 2  # continues past the tail
        el2.close()

    def test_output_cap(self):
        text, trunc = tool_output_cap("x" * 70_000)
        assert trunc and len(text) == 64_000
        assert tool_output_cap("short") == ("short", False)


class TestReplay:
    def _seed(self, tmp_path):
        el = EventLog(tmp_path / "events.jsonl")
        el.append("session.start", objective="recon http://t", provider="zai")
        el.append("iteration", n=1, phase="informational")
        el.append("assistant.message", content='{"action": "use_tool", "tool_name": "http_request"}')
        el.append("tool.call", id="t1", name="http_request", args={"url": "http://t/"})
        el.append("tool.result", id="t1", ok=True, duration_ms=412, output="200 OK")
        el.append("iteration", n=2, phase="informational")
        el.append("phase.transition", **{"from": "informational", "to": "exploitation"})
        el.append("guidance.delivered", text="focus on login", source="pause")
        el.close()
        return tmp_path / "events.jsonl"

    def test_state_reconstructed(self, tmp_path):
        st = replay(self._seed(tmp_path))
        assert st["original_objective"] == "recon http://t"
        assert st["current_iteration"] == 2
        assert st["current_phase"] == "exploitation"  # transition applied
        assert any(m["role"] == "assistant" for m in st["messages"])
        assert any("focus on login" in str(m.get("content")) for m in st["messages"])
        tr = st["execution_trace"]
        assert (
            len(tr) == 1
            and tr[0]["tool_name"] == "http_request"
            and tr[0]["success"]
            and "200 OK" in tr[0]["tool_output"]
        )

    def test_completion_absent_by_contract(self, tmp_path):
        st = replay(self._seed(tmp_path))
        assert "completion_reason" not in st  # replayed runs must run

    def test_failed_tool_carries_error_kind(self, tmp_path):
        el = EventLog(tmp_path / "e.jsonl")
        el.append("session.start", objective="x")
        el.append("tool.call", id="t9", name="nmap_scan", args={})
        el.append("tool.result", id="t9", ok=False, error_kind="missing_binary", output="Error: nmap not installed")
        el.close()
        st = replay(tmp_path / "e.jsonl")
        assert st["execution_trace"][0]["success"] is False
        assert st["execution_trace"][0]["error_kind"] == "missing_binary"

    def test_messages_trimmed_to_sje_cap(self, tmp_path):
        el = EventLog(tmp_path / "e.jsonl")
        el.append("session.start", objective="x")
        for i in range(120):
            el.append("assistant.message", content=f'{{"i": {i}}}')
        el.close()
        st = replay(tmp_path / "e.jsonl")
        assert len(st["messages"]) == 80
        assert "replayed from the event log" in st["messages"][0]["content"]
