"""Self-critique — post-engagement LLM review writes learnings.

Contract: after an engagement, the critique pass produces structured
self-assessment; the report lands in outputs/reports/, tactics land in
the knowledge graph as [self-critique] behavior constraints. Never
fatal: LLM failure, parse failure, or config-off all return None.
"""

import json

from suijin.modules.agent.lib.critique import run_self_critique


def _state(trace=None):
    if trace is None:
        trace = [
            {"tool_name": "whatweb_scan", "tool_args": {"url": "http://t"}, "success": True, "duration_ms": 800},
            {"tool_name": "search_kb", "tool_args": {"keyword": "sqli"}, "success": False, "duration_ms": 12},
        ]
    return {
        "objective": "own the lab",
        "completion_reason": "complete",
        "execution_trace": trace,
        "target_info": {"name": "lab-target"},
        "cost_usd": 0.02,
    }


def _good_llm(payload=None):
    payload = payload or {
        "what_worked": ["nmap first"],
        "what_wasted": ["3 calls on /api when /api/v2 was live"],
        "missed_leads": ["server header leaked version"],
        "tactics_to_remember": ["check /api/v2 before /api on this stack"],
        "verdict": "B+ — solid recon, sloppy endpoint triage",
    }

    async def gen(messages, config=None, **kw):
        return json.dumps(payload)

    return gen


class TestCritique:
    def test_full_pass_writes_report_and_kg(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        recorded = []

        from suijin.modules.redteam.lib.intel import knowledge_graph as kg

        monkeypatch.setattr(kg, "add_constraint", lambda *a, **k: recorded.append((a, k)))

        out = run_self_critique(
            objective="own the lab",
            final_state=_state(),
            config={},
            generate_fn=_good_llm(),
            thread_id="eng-42",
        )
        assert out and out["verdict"].startswith("B+")
        # report written
        reports = list((tmp_path / "outputs" / "reports").glob("critique_eng-42.md"))
        assert reports and "Tactics to remember" in reports[0].read_text()
        assert "/api/v2" in reports[0].read_text()
        # KG learnings recorded with the prefix + heuristic confidence
        assert recorded and recorded[0][0][1] == "behavior"
        assert recorded[0][0][2].startswith("[self-critique]")
        assert recorded[0][1]["confidence"] == 0.8

    def test_config_off_returns_none(self):
        assert (
            run_self_critique(
                objective="x",
                final_state=_state(),
                config={"self_critique": False},
                generate_fn=_good_llm(),
            )
            is None
        )

    def test_empty_trace_returns_none(self):
        assert (
            run_self_critique(
                objective="x",
                final_state=_state(trace=[]),
                config={},
                generate_fn=_good_llm(),
            )
            is None
        )

    def test_llm_failure_is_not_fatal(self):
        async def boom(messages, config=None, **kw):
            raise RuntimeError("provider down")

        assert run_self_critique(objective="x", final_state=_state(), config={}, generate_fn=boom) is None

    def test_garbage_output_returns_none(self):
        async def garbage(messages, config=None, **kw):
            return "not json at all"

        assert run_self_critique(objective="x", final_state=_state(), config={}, generate_fn=garbage) is None

    def test_provider_error_string_returns_none(self):
        async def err(messages, config=None, **kw):
            return "Error: API key not set"

        assert run_self_critique(objective="x", final_state=_state(), config={}, generate_fn=err) is None
