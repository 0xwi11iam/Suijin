"""Red-teamer console UI — transcript blocks, loot detection, reasoning toggle."""

import json

import pytest
from rich.console import Console

from suijin.modules.redteam.lib.red.console_ui import (
    UI_STATE,
    EngagementUI,
    loot_in,
    toggle_reasoning,
)


@pytest.fixture(autouse=True)
def _fresh_state():
    saved = dict(UI_STATE)
    UI_STATE.update(
        {
            "show_reasoning": True,
            "flags": [],
            "creds": [],
            "fireteams": 0,
            "last_reasoning": "",
            "last_result_success": True,
        }
    )
    yield
    UI_STATE.update(saved)


def _ui(width: int = 100) -> tuple[EngagementUI, Console]:
    c = Console(record=True, width=width, force_terminal=True, no_color=False)
    return EngagementUI(c, objective="test"), c


class TestLoot:
    def test_flags_dedupe(self):
        flags, creds = loot_in("a FLAG{ONE} b FLAG{ONE} FLAG{TWO}")
        assert flags == ["FLAG{ONE}", "FLAG{TWO}"]
        assert creds == []

    def test_credential_kinds(self):
        blob = "AKIAIOSFODNN7EXAMPLE sk-abcdefghijklmnopqrst1234x ghp_" + "a" * 36
        flags, creds = loot_in(blob)
        kinds = [k for k, _ in creds]
        assert "AWS key" in kinds and "OpenAI-style key" in kinds and "GitHub token" in kinds

    def test_jwt_and_slack(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N65IWDpmNfXPU4HuXqoj0k"
        flags, creds = loot_in(f"token={jwt} xoxb-123456789-abcdefghij")
        assert any(k == "JWT" for k, _ in creds)
        assert any(k == "Slack token" for k, _ in creds)

    def test_benign_text_has_no_loot(self):
        assert loot_in("Status: 200 OK — normal page content, nothing secret") == ([], [])

    def test_regex_rejects_whitespace_flag(self):
        assert loot_in("FLAG{has space}") == ([], [])


class TestTranscript:
    def test_iteration_renders_as_one_panel(self):
        ui, c = _ui()
        ui.iteration_header(6, "informational")
        ui.thinking("Crafting an XSS payload to test the search field")
        ui.reasoning("Search reflects input unencoded")
        ui.tool("execute_terminal", {"cmd": 'curl -s "http://t/search" --data "q=x"'})
        ui.output("Status: 200\nOK")
        out = c.export_text()
        assert "#6 · informational" in out  # panel title carries number+phase
        assert "thinking" in out and "XSS payload" in out
        assert "Search reflects input unencoded" in out  # reasoning under thinking, no label
        assert ":: why ::" not in out
        assert "execute_terminal" in out
        assert "Status: 200" in out

    def test_reasoning_toggle(self):
        ui, c = _ui()
        ui.iteration_header(1, "informational")
        ui.reasoning("the why sentence")
        ui.tool("nmap", {"target": "10.0.0.1"})
        ui.output("scan done")
        assert "the why sentence" in c.export_text()  # shown by default now
        toggle_reasoning(Console(record=True, width=100, force_terminal=True))
        c2 = Console(record=True, width=100, force_terminal=True)
        ui.console = c2
        ui.iteration_header(2, "informational")
        ui.reasoning("hidden why")
        ui.tool("nmap", {"target": "10.0.0.1"})
        ui.output("scan done")
        assert "hidden why" not in c2.export_text()

    def test_loot_lines_and_counters(self):
        ui, c = _ui()
        ui.output("body: FLAG{SO_TUFF} and AKIAIOSFODNN7EXAMPLE")
        out = c.export_text()
        assert "Flag collected!" in out and "FLAG{SO_TUFF}" in out
        assert "Credentials harvested!" in out
        assert UI_STATE["flags"] == ["FLAG{SO_TUFF}"]
        assert UI_STATE["creds"][0][0] == "AWS key"
        # duplicate loot not re-announced
        n = c.export_text().count("Flag collected!")
        ui.output("again FLAG{SO_TUFF}")
        assert c.export_text().count("Flag collected!") == n

    def test_http_request_uses_json_lexer(self):
        ui, c = _ui()
        ui.iteration_header(2, "informational")
        ui.tool("http_request", {"method": "POST", "url": "http://t/x", "body": "a=1"})
        ui.flush_open()
        assert '"method"' in c.export_text()

    def test_blocked_output_rendered(self):
        ui, c = _ui()
        ui.iteration_header(3, "informational")
        ui.tool("nmap", {"target": "10.0.0.1"})
        ui.output("policy: target outside allowed scopes")
        assert "BLOCKED" in c.export_text()

    def test_graceful_errors(self):
        from suijin.modules.redteam.lib.red.console_ui import graceful_error, is_error

        # the exact wall of text from the drforst.org field run
        raw = (
            "HTTP Error: HTTPSConnectionPool(host='drforst.org', port=443): Max retries exceeded "
            "with url / (Caused by NameResolutionError(\"HTTPSConnection(host='drforst.org', port=443): "
            "Failed to resolve 'drforst.org' ([Errno 8] nodename nor servname provided, or not known)\"))"
        )
        assert is_error(raw)
        short = graceful_error(raw)
        assert "HTTPSConnectionPool" not in short and "Max retries" not in short
        assert "drforst.org" in short and len(short) < 120
        assert graceful_error("Error: nmap: command not found") == "nmap not installed"
        assert graceful_error("Tool error: connection to 10.0.0.5 port 22 refused") is not None

    def test_error_output_renders_as_error_panel(self):
        ui, c = _ui()
        ui.iteration_header(4, "informational")
        ui.tool("http_request", {"method": "GET", "url": "https://nope.invalid"})
        ui.output(
            "HTTP Error: HTTPSConnectionPool(host='nope.invalid', port=443): Max retries exceeded with url / "
            "(Caused by NameResolutionError(\"Failed to resolve 'nope.invalid'\"))"
        )
        out = c.export_text()
        assert "error" in out and "could not resolve nope.invalid (DNS)" in out
        assert "HTTPSConnectionPool" not in out

    def test_fireteam_and_strip_counters(self):
        ui, c = _ui()
        ui.iteration_header(2, "informational")
        ui.fireteam("Fireteam deployed: 3 specialist(s)")
        ui.flush_open()
        assert UI_STATE["fireteams"] == 1
        assert "Fireteam deployed" in c.export_text()

    def test_planned_steps(self):
        ui, c = _ui()
        ui.iteration_header(2, "informational")
        ui.planned_steps([{"tool_name": "http_request"}, {"tool_name": "nmap"}])
        ui.flush_open()
        out = c.export_text()
        assert "2 more step(s)" in out and "http_request" in out

    def test_supervisor_oracle_drift(self):
        ui, c = _ui()
        ui.iteration_header(2, "informational")
        ui.supervisor("switch approach")
        ui.oracle(["try blind sqli"])
        ui.drift("coverage stalled")
        ui.flush_open()
        out = c.export_text()
        assert "Supervisor" in out and "Oracle" in out and "Drift" in out

    def test_phase_transition(self):
        ui, c = _ui()
        ui.iteration_header(2, "informational")
        ui.phase_transition("exploitation", "creds found")
        ui.flush_open()
        assert "phase -> exploitation" in c.export_text()

    def test_ask_renders_question_once(self):
        ui, c = _ui()
        ui.iteration_header(2, "informational")
        ui.ask("Continue with destructive tests?")
        assert c.export_text().count("Continue with destructive") == 1

    def test_done_summarizes_loot(self):
        ui, c = _ui()
        ui.iteration_header(2, "informational")
        ui.loot("FLAG{X}")
        ui.done(4, 5, "exploitation", 0.5, "Objective complete")
        out = c.export_text()
        assert "Done:" in out and "FLAG{X}" in out

    def test_syntax_falls_back_on_exotic_tool(self):
        ui, c = _ui()
        ui.iteration_header(2, "informational")
        ui.tool("some_unknown_pack_tool", {"weird": "args"})
        ui.flush_open()
        assert "some_unknown_pack_tool" in c.export_text()

    def test_non_terminal_console_no_crash(self):
        c = Console(record=True, width=80, force_terminal=False)
        ui = EngagementUI(c)
        ui.iteration_header(1, "informational")
        ui.tool("execute_terminal", {"cmd": "echo hi"})
        ui.flush_open()
        assert "echo hi" in c.export_text()

    def test_waiting_spinner_and_strip(self):
        ui, c = _ui()
        ui.start()
        assert ui._strip() is not None  # spinner state renders before first iteration
        ui.iteration_header(1, "informational")
        ui.waiting(True)
        assert ui._strip() is not None
        ui.stop()


class TestPricing:
    def test_missing_models_now_priced(self):
        from suijin.modules.providers.lib import _price_for

        assert _price_for("glm-4.6") is not None
        assert _price_for("Qwen/Qwen3-Coder-480B-A35B-Instruct") is not None
        assert _price_for("zai-org/GLM-5.1") is not None

    def test_case_insensitive_match(self):
        from suijin.modules.providers.lib import _price_for

        # the DEFAULT huggingface model id — case drift previously fell to DEFAULT_RATE
        assert _price_for("deepseek-ai/DeepSeek-V4-Flash") == _price_for("deepseek-v4-flash")
        assert _price_for("deepseek-ai/DeepSeek-V4-Pro") == _price_for("deepseek-v4-pro")

    def test_unknown_model_returns_none(self):
        from suijin.modules.providers.lib import _price_for

        assert _price_for("totally-unknown-model-xyz") is None

    def test_priced_flag_true_semantics(self):
        """priced=False once ANY call uses the fallback rate."""
        from suijin.modules.providers.lib import USAGE, _record_usage, reset_usage

        reset_usage()
        _record_usage("t", "glm-5.3", 1000, 1000)
        assert USAGE["priced"] is True
        _record_usage("t", "totally-unknown-model-xyz", 1000, 1000)
        assert USAGE["priced"] is False

    def test_estimate_fallback_on_missing_usage_zai(self, monkeypatch):
        """Regression: the estimate path referenced an unbound `text` —
        UnboundLocalError swallowed -> ZERO tokens recorded."""
        from suijin.modules.providers import lib as pl

        calls = {}

        class _Resp:
            status_code = 200

            def json(self):
                return {
                    "choices": [{"message": {"content": "hello world reply"}}],
                    "usage": {},
                }  # usage present but empty

        def fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002
            calls["url"] = url
            return _Resp()

        monkeypatch.setenv("ZAI_API_KEY", "k")
        monkeypatch.setattr(pl.req, "post", fake_post)
        pl.reset_usage()
        out = pl.generate([{"role": "user", "content": "hi"}], {"provider": "zai"})
        assert out == "hello world reply"
        assert pl.USAGE["calls"] == 1
        assert pl.USAGE["estimated_calls"] == 1  # estimated, not zero-count
        assert pl.USAGE["input_tokens"] > 0

    def test_subagent_llm_timeout_env(self, monkeypatch):
        from suijin.modules.agent.lib.nodes import subagent_node as sn

        monkeypatch.setenv("SUIJIN_SUBAGENT_LLM_TIMEOUT", "90")
        assert sn._llm_timeout() == 90
        monkeypatch.setenv("SUIJIN_SUBAGENT_LLM_TIMEOUT", "1")  # clamped to min 5
        assert sn._llm_timeout() == 5
        monkeypatch.delenv("SUIJIN_SUBAGENT_LLM_TIMEOUT")
        assert sn._llm_timeout() == sn.LLM_TIMEOUT

    def test_subagent_timeout_retry(self):
        """One timeout triggers a patient retry; decision still lands."""
        import asyncio

        from suijin.modules.agent.lib.nodes.subagent_node import run_subagent

        state = {"n": 0}

        async def gen(messages, config=None, **kw):
            state["n"] += 1
            if state["n"] == 1:
                raise asyncio.TimeoutError()
            return json.dumps({"action": "complete", "completion_reason": "done", "thought": "t"})

        res = asyncio.run(
            run_subagent("probe http://127.0.0.1:1 for flaws", generate_fn=gen, route_tool_fn=lambda *a: "ok")
        )
        assert res.success
        assert state["n"] == 2  # retried exactly once

    def test_subagent_double_timeout_counts_failure(self):
        import asyncio

        from suijin.modules.agent.lib.nodes.subagent_node import run_subagent

        state = {"n": 0}

        async def gen(messages, config=None, **kw):
            state["n"] += 1
            if state["n"] <= 2:
                raise asyncio.TimeoutError()
            return json.dumps({"action": "complete", "completion_reason": "done", "thought": "t"})

        res = asyncio.run(
            run_subagent("probe http://127.0.0.1:1 for flaws", generate_fn=gen, route_tool_fn=lambda *a: "ok")
        )
        assert res.success
        assert state["n"] == 3  # two timeouts (step 1 + retry) then clean step 2
        assert isinstance(res.findings, str)


class TestEventPlumbing:
    def test_merge_replaces_trace_step_by_iteration(self):
        from suijin.modules.agent.lib.agent_graph import _merge_state

        left = {"execution_trace": [{"iteration": 1, "success": True, "thought": "a"}]}
        right = {"execution_trace": [{"iteration": 1, "success": False, "error_class": "tool_internal_error"}]}
        merged = _merge_state(left, right)
        assert len(merged["execution_trace"]) == 1
        assert merged["execution_trace"][0]["success"] is False

    def test_merge_appends_new_iterations(self):
        from suijin.modules.agent.lib.agent_graph import _merge_state

        left = {"execution_trace": [{"iteration": 1}]}
        right = {"execution_trace": [{"iteration": 2}]}
        assert len(_merge_state(left, right)["execution_trace"]) == 2

    @pytest.mark.asyncio
    async def test_think_clears_stale_step_on_phase_transition(self):
        from suijin.modules.agent.lib.nodes import think_node as tn

        decision = {
            "action": "transition_phase",
            "phase_transition": {"to_phase": "exploitation", "reason": "found creds"},
            "thought": "moving on",
        }

        async def gen(messages, config=None, **kw):
            return json.dumps(decision)

        updates = await tn.think_node(
            {
                "messages": [],
                "execution_trace": [],
                "current_iteration": 3,
                "current_phase": "informational",
                "phase_history": [],
                "_current_step": {"tool_name": "nmap", "tool_args": {"target": "x"}},  # stale
                "todo_list": [],
                "pending_questions": [],
            },
            generate_fn=gen,
        )
        assert updates.get("_current_step") == {}  # no re-execution of nmap
        assert updates.get("current_phase") == "exploitation"

    @pytest.mark.asyncio
    async def test_execute_backfills_trace(self):
        from suijin.modules.agent.lib.nodes.execute_tool_node import execute_tool_node

        step = {
            "tool_name": "http_request",
            "tool_args": {"method": "GET", "url": "http://127.0.0.1:1/"},
            "iteration": 7,
            "phase": "recon",
            "thought": "probe",
        }
        out = await execute_tool_node(
            {"_current_step": dict(step), "current_phase": "recon"},
            route_tool_fn=lambda n, a, c: "Error: connection refused",
        )
        trace = out["execution_trace"]
        assert trace and trace[0]["iteration"] == 7
        assert trace[0]["success"] is False
        assert trace[0]["tool_output"].startswith("Error:")

    def test_prompt_requests_reasoning(self):
        from suijin.modules.agent.lib.prompts.base import build_agent_system_prompt

        blob = build_agent_system_prompt({})
        assert '"reasoning"' in blob and ":: why ::" not in blob  # requested, not leaked


class TestInstallScript:
    def test_bash_syntax(self):
        import subprocess

        r = subprocess.run(["bash", "-n", "install.sh"], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    def test_dev_flag_help(self):
        import subprocess
        from pathlib import Path

        repo = Path(__file__).resolve().parents[3]
        r = subprocess.run(["bash", str(repo / "install.sh"), "--help"], capture_output=True, text=True)
        assert "--dev[=PATH]" in r.stdout
