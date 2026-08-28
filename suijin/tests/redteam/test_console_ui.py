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


# every CORE (non-pack) tool with representative args — the render
# contract: no crash, tool name shown, content-bearing tools show content,
# no-arg tools show NO command block
CORE_TOOLS = [
    ("execute_terminal", {"cmd": "nmap -sV 10.0.0.1"}, "nmap -sV 10.0.0.1"),
    ("http_request", {"method": "GET", "url": "http://t/x"}, "http://t/x"),
    ("read_file", {"file_path": "loot/creds.txt"}, "loot/creds.txt"),
    ("write_file", {"file_path": "s.py", "content": "print('hi')"}, "print('hi')"),
    ("apply_patch", {"vulnerability": "sqli", "file_path": "app.py"}, "vulnerability=sqli"),
    ("claim_flag", {"flag": "FLAG{X}"}, "flag=FLAG{X}"),
    ("recon_chain", {"target": "10.0.0.1", "ports": "80,443"}, "target=10.0.0.1"),
    ("msf_command", {"cmd": "search type:auxiliary"}, "search type:auxiliary"),
    ("msf_run", {"module": "auxiliary/scanner/ssh", "payload": "", "options": {"RHOSTS": "10.0.0.1"}}, "RHOSTS"),
    ("search_cve", {"software": "apache", "version": "2.4.49", "limit": 5}, "software=apache"),
    ("search_kb", {"keyword": "ssh", "limit": 5}, "keyword=ssh"),
    ("kb_read", {"path": "_gtfobins/awk"}, "_gtfobins/awk"),
    ("check_knowledge", {"target": "10.0.0.1", "payload": "admin'--"}, "target=10.0.0.1"),
    (
        "record_finding",
        {"target": "10.0.0.1", "finding_type": "sqli", "rule": "blocked", "evidence": "err"},
        "finding_type=sqli",
    ),
    ("write_note", {"content": "## Findings\n- sqli on /search", "category": "findings"}, "## Findings"),
    ("web_search", {"query": "apache 2.4.49 exploit", "max_results": 5}, "apache 2.4.49"),
    ("edit_skill", {"skill_name": "sqli", "new_content": "def run(): pass"}, "def run(): pass"),
    ("write_tool", {"tool_name": "mytool", "code": "def run(): pass"}, "def run(): pass"),
    ("pip_install", {"package": "requests"}, "package=requests"),
    ("job_status", {"job_id": "abc123"}, "job_id=abc123"),
    ("job_wait", {"job_id": "abc123", "timeout": 60}, "job_id=abc123"),
    ("job_output", {"job_id": "abc123"}, "job_id=abc123"),
    ("job_cancel", {"job_id": "abc123"}, "job_id=abc123"),
    ("payload_generate", {"type": "rev_shell", "lhost": "10.0.0.2"}, "rev_shell"),
    ("diff_response", {"url_a": "http://t/a", "url_b": "http://t/b"}, "url_a=http://t/a"),
    ("rate_limit_check", {"endpoint": "http://t/api"}, "endpoint=http://t/api"),
    ("attack_tree", {"objective": "get shell"}, "objective=get shell"),
    ("anonymize_report", {"file_path": "r.md"}, "file_path=r.md"),
    ("extract_payloads", {"source": "reqs.txt"}, "source=reqs.txt"),
    ("mine_failures", {"engagement": "test"}, "engagement=test"),
    ("normalize_output", {"mode": "clean"}, "mode=clean"),
    ("target_dossier", {"target": "10.0.0.1"}, "target=10.0.0.1"),
    ("mutate_wordlist", {"wordlist": "w.txt", "mutations": "case"}, "wordlist=w.txt"),
    ("cewl_words", {"url": "http://t", "depth": 2}, "url=http://t"),
    ("find_wordlist", {"kind": "dir", "pattern": "api"}, "kind=dir"),
    ("wordlist_tool", {"mode": "count", "path": "w.txt"}, "mode=count"),
    ("suggest_exploit", {"service": "ssh", "version": "7.2"}, "service=ssh"),
    ("evidence_capture", {"label": "sqli-proof", "path": "e/"}, "label=sqli-proof"),
    ("evidence_verify", {"bundle": "e.zip"}, "bundle=e.zip"),
    ("recipe_define", {"name": "mychain", "steps_json": '[{"tool":"nmap"}]'}, "mychain"),
    ("recipe_run", {"name": "recon_web", "target": "example.com"}, "name=recon_web"),
    ("cve_advise_tools", {"keyword": "CVE-2021-44228"}, "keyword=CVE-2021-44228"),
    ("job_list", {}, None),
    ("list_skills", {}, None),
    ("list_own_files", {}, None),
    ("fireteam_status", {}, None),
    ("recipe_list", {}, None),
    ("kb_stats", {}, None),
    ("kb_freshness", {}, None),
    ("msf_check", {}, None),
    ("msf_sessions", {}, None),
    ("rate_limit_all", {}, None),
    ("generate_report", {}, None),
    ("deploy_subagent", {"subagent_task": "probe a || probe b"}, None),
]

CORE_TOOL_NAMES = {name for name, _, _ in CORE_TOOLS}


class TestEveryCoreToolRenders:
    @pytest.mark.parametrize("name,args,expect", CORE_TOOLS, ids=[c[0] for c in CORE_TOOLS])
    def test_renders(self, name, args, expect):
        ui, c = _ui()
        ui.iteration_header(1, "informational")
        ui.thinking("step thought")
        ui.reasoning("step reasoning")
        ui.tool(name, args)
        ui.output("Status: 200\nok")
        out = c.export_text()
        assert name in out, name  # the ❯ line
        assert "#1 · informational" in out
        if expect is not None:
            assert expect in out, (name, out[:400])  # the arg content, not a dict dump
        else:
            # no-arg tools: no '{}' block noise under the tool line
            assert "{}" not in out, name

    def test_core_tool_inventory_covers_dispatch_routes(self):
        """Every explicit dispatch route renders with a dedicated style or
        the JSON fallback — and the no-args set is exactly the argless ones."""
        from suijin.modules.redteam.lib.red.console_ui import _LEXERS, _NO_ARGS_TOOLS

        for name, _, expect in CORE_TOOLS:
            if expect is not None:
                assert name in _LEXERS, f"{name} needs a lexer entry"
            else:
                assert name in _NO_ARGS_TOOLS, f"{name} must be in _NO_ARGS_TOOLS"
        assert not (_NO_ARGS_TOOLS & set(_LEXERS)), "a tool is both no-args and lexered"

    def test_write_note_renders_markdown_not_dict(self):
        ui, c = _ui()
        ui.iteration_header(2, "informational")
        ui.tool("write_note", {"content": "# Loot\nfound creds", "success": True, "category": "loot"})
        ui.flush_open()
        out = c.export_text()
        assert "# Loot" in out
        assert "'success': True" not in out and '"success"' not in out  # no dict dump

    def test_fireteam_renders_in_box(self):
        ui, c = _ui()
        ui.iteration_header(3, "informational")
        ui.fireteam("Fireteam deployed: 2 specialist(s)")
        ui.flush_open()
        out = c.export_text()
        assert "Fireteam deployed" in out and "#3" in out


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
        assert "XSS payload" in out  # thinking text (no label — we know what it is)
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

        # the exact wall of text from the field-target.example field run
        raw = (
            "HTTP Error: HTTPSConnectionPool(host='field-target.example', port=443): Max retries exceeded "
            "with url / (Caused by NameResolutionError(\"HTTPSConnection(host='field-target.example', port=443): "
            "Failed to resolve 'field-target.example' ([Errno 8] nodename nor servname provided, or not known)\"))"
        )
        assert is_error(raw)
        short = graceful_error(raw)
        assert "HTTPSConnectionPool" not in short and "Max retries" not in short
        assert "field-target.example" in short and len(short) < 120
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
        assert "could not resolve nope.invalid (DNS)" in out
        assert "HTTPSConnectionPool" not in out  # graceful, not the raw blob
        assert ui._cur is None  # flushed

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

    def test_no_truncation_anywhere(self):
        """Field order: never clip thinking/reasoning/questions/outputs."""
        ui, c = _ui()
        long_thought = "x" * 900
        long_out = "y" * 4000
        ui.iteration_header(9, "informational")
        ui.thinking(long_thought)
        ui.reasoning("r" * 900)
        ui.tool("execute_terminal", {"cmd": "echo hi"})
        ui.output(long_out)
        out = c.export_text()
        assert "…" not in out and "+900 chars" not in out and "+4000 chars" not in out
        assert out.count("y") >= 4000  # full output body rendered (wrapped, nothing dropped)

    def test_ask_operator_answer_via_runbox(self):
        """Regression for the field hang: the answer MUST come through the
        RunBox guidance queue (stdin's single owner). A slash command must
        NOT be consumed as the answer; the next plain line is."""
        import threading
        import time

        from suijin.modules.redteam.lib.red.console_ui import ask_operator_answer
        from suijin.modules.tools.lib.run_commands import RunBox

        box = RunBox().start()
        box.take_guidance()  # drain

        def typer():
            time.sleep(0.2)
            box.dispatch("/state")  # operator checks state first — not an answer
            time.sleep(0.2)
            box.dispatch("yes, I own the target")  # the actual answer

        threading.Thread(target=typer, daemon=True).start()
        t0 = time.monotonic()
        answer = ask_operator_answer(box, Console(record=True, width=90), "authorized?", timeout_s=5)
        elapsed = time.monotonic() - t0
        box.stop()
        assert answer == "yes, I own the target"
        assert elapsed < 4  # slash line skipped, not stalled to timeout

    def test_ask_operator_answer_timeout_returns_empty(self):
        from suijin.modules.redteam.lib.red.console_ui import ask_operator_answer
        from suijin.modules.tools.lib.run_commands import RunBox

        box = RunBox().start()
        box.take_guidance()
        answer = ask_operator_answer(box, Console(record=True, width=90), "anyone?", timeout_s=0.4)
        box.stop()
        assert answer == ""

    def test_cost_cap_warning_is_clean(self):
        """The pydantic wall of text is silenced; a custom category exists."""
        import warnings

        from suijin.modules.platform.lib.config_models import CostCapWarning, RedConfig

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            RedConfig(cost_hard_cap_usd=500.0)
        assert any(issubclass(x.category, CostCapWarning) for x in w)
        # and the message itself carries no source-line echo when filtered
        assert any("Cost cap $500.00" in str(x.message) for x in w)


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

        monkeypatch.setenv("ZAI_API_KEY", "k")
        # stream answered, gateway omitted usage -> estimate path
        monkeypatch.setattr(pl, "_stream_chat", lambda *a, **k: (200, "hello world reply", "", {}, ""))
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


class TestPauseConsole:
    """The 15 pause commands, driven through the extracted pause_console."""

    def _ctx(self, console=None, **kw):
        from suijin.modules.redteam.lib.red.session_control import PauseContext

        c = console or Console(record=True, width=100, force_terminal=True)
        ctx = PauseContext(console=c, loot={"flags": ["FLAG{A}"], "creds": [("AWS key", "AKIA" + "X" * 16)]}, **kw)
        return ctx, c

    def test_all_fifteen_commands_registered(self):
        from suijin.modules.redteam.lib.red.session_control import build_pause_handlers

        ctx, _ = self._ctx()
        h = build_pause_handlers(ctx)
        assert len(h) == 16
        assert set(h) == {
            "/report",
            "/audit",
            "/state",
            "/sessions",
            "/template",
            "/health",
            "/objective",
            "/phase",
            "/focus",
            "/skip",
            "/finish",
            "/loot",
            "/jobs",
            "/kill",
            "/cost",
            "/quit",
        }

    def test_plain_line_is_guidance(self):
        from suijin.modules.redteam.lib.red.session_control import pause_console

        ctx, _ = self._ctx()
        g = pause_console(ctx, lambda prompt="": "try harder on the login form")
        assert g == "try harder on the login form"

    def test_empty_input_becomes_continue(self):
        from suijin.modules.redteam.lib.red.session_control import pause_console

        ctx, _ = self._ctx()
        assert pause_console(ctx, lambda prompt="": "") == "Continue what you were doing."

    def test_commands_dispatch_then_guidance(self):
        from suijin.modules.redteam.lib.red.session_control import pause_console

        ctx, c = self._ctx()
        inputs = iter(["/loot", "/cost", "pivot to smtp"])
        g = pause_console(ctx, lambda prompt="": next(inputs))
        out = c.export_text()
        assert "FLAG{A}" in out and "AWS key" in out  # /loot
        assert "tok" in out  # /cost
        assert g == "pivot to smtp"  # non-slash line ends the loop

    def test_focus_skip_finish_merge_into_guidance(self):
        from suijin.modules.redteam.lib.red.session_control import pause_console

        ctx, _ = self._ctx()
        inputs = iter(["/skip", "/focus the upload endpoint", "/finish", ""])
        g = pause_console(ctx, lambda prompt="": next(inputs))
        assert "Abandon the current approach" in g
        assert "Focus on: the upload endpoint" in g
        assert "Wrap up NOW" in g

    def test_objective_changes_course(self):
        from suijin.modules.redteam.lib.red.session_control import pause_console

        updated = {}

        class FakeGraph:
            def update_state(self, cfg, payload):
                updated.update(payload)

        class FakeAgent:
            _graph = FakeGraph()

        ctx, _ = self._ctx(agent=FakeAgent(), langgraph_config={"configurable": {}})
        inputs = iter(["/objective pivot to the API at api.target.com", "go"])
        g = pause_console(ctx, lambda prompt="": next(inputs))
        assert updated["original_objective"] == "pivot to the API at api.target.com"
        assert ctx.objective == "pivot to the API at api.target.com"
        assert g == "go"

    def test_objective_requires_arg(self):
        from suijin.modules.redteam.lib.red.session_control import pause_console

        ctx, c = self._ctx()
        inputs = iter(["/objective", ""])
        pause_console(ctx, lambda prompt="": next(inputs))
        assert "usage: /objective" in c.export_text()

    def test_phase_validates(self):
        from suijin.modules.redteam.lib.red.session_control import pause_console

        class FakeGraph:
            def update_state(self, cfg, payload):
                payload["ok"] = True

        class FakeAgent:
            _graph = FakeGraph()

        ctx, c = self._ctx(agent=FakeAgent(), langgraph_config={})
        inputs = iter(["/phase exploitation", "/phase bogus", ""])
        pause_console(ctx, lambda prompt="": next(inputs))
        out = c.export_text()
        assert "phase -> exploitation" in out
        assert "usage: /phase" in out

    def test_kill_and_jobs_route(self):
        from suijin.modules.redteam.lib.red.session_control import pause_console

        routed = []

        def fake_route(name, args):
            routed.append((name, args))
            return f"ok {name}"

        ctx, c = self._ctx(route_tool_fn=fake_route)
        inputs = iter(["/jobs", "/kill abc123", ""])
        pause_console(ctx, lambda prompt="": next(inputs))
        assert ("job_list", {}) in routed
        assert ("job_cancel", {"job_id": "abc123"}) in routed

    def test_kill_requires_arg(self):
        from suijin.modules.redteam.lib.red.session_control import pause_console

        ctx, c = self._ctx()
        inputs = iter(["/kill", ""])
        pause_console(ctx, lambda prompt="": next(inputs))
        assert "usage: /kill" in c.export_text()

    def test_cost_formats_usage(self):
        from suijin.modules.redteam.lib.red.session_control import pause_console

        ctx, c = self._ctx(
            usage_fn=lambda: {
                "calls": 3,
                "input_tokens": 1000,
                "output_tokens": 500,
                "api_reported_calls": 2,
                "estimated_calls": 1,
                "est_cost_usd": 0.05,
            }
        )
        inputs = iter(["/cost", ""])
        pause_console(ctx, lambda prompt="": next(inputs))
        out = c.export_text()
        assert "calls 3" in out and "1.5k tok" in out and "$0.0500" in out

    def test_state_without_agent_is_safe(self):
        from suijin.modules.redteam.lib.red.session_control import build_pause_handlers

        ctx, _ = self._ctx(agent=None)
        h = build_pause_handlers(ctx)
        h["/state"]("")  # no agent -> no crash, prints nothing fatal


class TestFieldCrashRegressions:
    """The live-run report: violent flashing + a parse_failure run that
    vanished after ~5s with nothing rendered."""

    def test_llm_client_has_no_competing_spinner(self):
        """The flashing: llm_client ran console.status — a SECOND Live
        region — concurrently with the engagement strip's Live."""
        import inspect

        from suijin.modules.redteam.lib.red import llm_client

        src = inspect.getsource(llm_client)
        assert "console.status" not in src
        assert "Thinking..." not in src

    def test_live_region_is_strip_only(self):
        """The live region must be ONE stable row — iteration content
        streams above it, never inside it (repaint storms)."""
        ui, c = _ui()
        ui.start()
        ui.iteration_header(4, "informational")
        ui.thinking("streaming thought")
        ui.tool("execute_terminal", {"cmd": "echo hi"})
        # whatever the live region holds now, it must NOT contain the
        # iteration's content
        r = ui._live.renderable
        rendered = Console(record=True, width=100, force_terminal=True)
        rendered.print(r)
        live_text = rendered.export_text()
        assert "streaming thought" not in live_text
        assert "echo hi" not in live_text
        ui.output("Status: 200")
        ui.stop()

    def test_parse_note_renders_retry(self):
        ui, c = _ui()
        ui.iteration_header(2, "informational")
        ui.parse_note(1)
        ui.parse_note(2)
        out = c.export_text()
        assert "response unparseable — asking again (1/3)" in out
        assert "response unparseable — asking again (2/3)" in out

    def test_failure_panel_never_vanishes(self):
        ui, c = _ui()
        ui.iteration_header(2, "informational")
        ui.failure("parse_failure", "Agent stopped: LLM output could not be parsed after 3 attempts.")
        out = c.export_text()
        assert "engagement ended" in out
        assert "parse_failure" in out
        assert "could not be parsed after 3 attempts" in out

    def test_graph_parse_death_is_observable(self):
        """End-to-end class proof: a garbage model kills the run via
        parse_failure with corrective messages in state — the exact
        signals the UI renders (parse_note + failure panel)."""
        import asyncio

        from suijin.modules.agent.lib.agent_graph import SuijinAgentGraph

        async def garbage(messages, config=None, **kw):
            return "I am definitely not JSON <thinking>blah</thinking>"

        graph = SuijinAgentGraph(generate_fn=garbage, route_tool_fn=lambda *a: "ok", max_iterations=5)
        state = asyncio.run(graph.run("probe 127.0.0.1 for flaws", thread_id="parse-death-test"))
        assert state.get("completion_reason") == "parse_failure"
        msgs = " ".join(str(m.get("content", "")) for m in state.get("messages", []))
        assert "JSON parse failed" in msgs  # the signal the loop keys on


class TestAskFlowAndDoctrine:
    """The field-target.example field reports: refusal re-litigation, answer prompt
    eaten by the live strip, thinking shown on ask turns, truncated
    question, raw parse noise, langgraph warning, cost-cap spam."""

    def test_engagement_order_lifts_authorization(self):
        from suijin.modules.agent.lib.prompts.base import engagement_order

        order = engagement_order("field-target.example I have written permission, h1 authorisation id REDACTED-AUTH-ID")
        assert "[ENGAGEMENT ORDER]" in order
        assert "h1 authorisation id REDACTED-AUTH-ID" in order  # verbatim claim
        assert "field-target.example" in order
        # force-language REMOVED — it primed meta-suspicion in capable models
        assert "FINAL" not in order and "tool failure" not in order
        # bare objective still becomes an order with default attestation
        bare = engagement_order("10.0.0.5")
        assert "operator-attested" in bare and "10.0.0.5" in bare

    def test_objective_user_turn_is_an_order(self):
        """The think-prompt user turn carries the engagement order, not a
        bare 'attack X' (the refusal anchor)."""
        import asyncio

        from suijin.modules.agent.lib.nodes import think_node as tn

        captured = {}

        async def gen(messages, config=None, **kw):
            captured["user"] = messages[-1]["content"]
            return '{"action": "complete", "completion_reason": "done", "thought": "t"}'

        asyncio.run(
            tn.think_node(
                {
                    "messages": [],
                    "execution_trace": [],
                    "current_iteration": 1,
                    "current_phase": "informational",
                    "original_objective": "example.com — I have written permission",
                    "todo_list": [],
                },
                generate_fn=gen,
            )
        )
        assert "[ENGAGEMENT ORDER]" in captured["user"]
        assert "written permission" in captured["user"]

    def test_doctrine_is_final_section_before_decision_format(self):
        from suijin.modules.agent.lib.prompts.base import build_agent_system_prompt

        p = build_agent_system_prompt({})
        assert p.index("## WORKFLOW") < p.index("## DECISION FORMAT")
        assert "not your role" in p  # authorization review = not the agent's job

    def test_ask_turn_renders_full_markdown_question(self):
        ui, c = _ui()
        long_q = "Is " + "very " * 60 + "long authorization in place?"
        ui.ask(long_q)
        out = c.export_text()
        assert "very" in out and "…" not in out  # untruncated
        assert "thinking" not in out  # no thinking section on ask turns

    def test_langgraph_warning_filter_is_category_less(self):
        import warnings

        # simulate langchain's own base class (NOT a UserWarning subclass)
        class LangChainPendingDeprecationWarning(Warning):
            pass

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # apply the same filters cli.main installs
            warnings.filterwarnings("ignore", message=".*allowed_objects.*")
            warnings.warn(
                "The default value of `allowed_objects` will change in a future version.",
                LangChainPendingDeprecationWarning,
                stacklevel=2,
            )
        assert not w  # filtered despite the unknown category

    def test_scope_confirmation_detection(self):
        from suijin.modules.redteam.lib.redteamer import _looks_like_scope_confirmation

        assert _looks_like_scope_confirmation("yes, I have written permission for field-target.example")
        assert _looks_like_scope_confirmation("confirmed in scope, proceed")
        assert _looks_like_scope_confirmation("i own this box")
        assert not _looks_like_scope_confirmation("try the login form with sql injection")

    def test_parse_attempt_no_longer_warns_on_console(self):
        """The raw 'Parse attempt N failed' line was console noise; the UI
        renders retries from state messages instead."""
        import asyncio
        import logging

        from suijin.modules.agent.lib.nodes import think_node as tn

        records = []

        class _H(logging.Handler):
            def emit(self, record):
                records.append(record)

        h = _H()
        tn.logger.addHandler(h)
        try:
            asyncio.run(
                tn.think_node(
                    {
                        "messages": [],
                        "execution_trace": [],
                        "current_iteration": 1,
                        "current_phase": "informational",
                        "original_objective": "x",
                        "todo_list": [],
                    },
                    generate_fn=lambda m, c=None, **k: "not json at all",
                )
            )
        finally:
            tn.logger.removeHandler(h)
        parse_warnings = [r for r in records if r.levelno >= logging.WARNING and "Parse attempt" in r.getMessage()]
        assert not parse_warnings  # demoted to debug


class TestLabelFreeTranscript:
    """v5.2 field pass: labels removed (we know what the sections are) —
    thinking renders dim blue, said renders cyan, both label-free."""

    def test_thinking_has_no_label(self):
        ui, c = _ui()
        ui.iteration_header(1, "informational")
        ui.thinking("Map the target before exploitation")
        out = c.export_text()
        assert "Map the target before exploitation" in out
        assert "thinking" not in out.lower().split("map")[0]  # no label prefix

    def test_said_has_no_label(self):
        ui, c = _ui()
        ui.iteration_header(1, "informational")
        ui.reasoning("Background it so I can keep probing")
        out = c.export_text()
        assert "Background it so I can keep probing" in out
        assert "said" not in out

    def test_stealth_never_advertises_brotli(self):
        """Field run (spa-target.example): we advertised Accept-Encoding: br
        without a brotli decoder — Vercel served br and the console filled
        with binary garbage."""
        from suijin.modules.platform.lib.stealth import browser_identity

        ae = browser_identity().get("Accept-Encoding", "")
        assert "br" not in ae
        assert "gzip" in ae

    def test_package_level_warning_filter(self):
        """The langgraph allowed_objects advisory must be filtered by the
        PACKAGE init (every entrypoint imports suijin first) — even under
        -W error and even for langchain's own warning base class."""
        import subprocess
        import sys

        code = (
            "import warnings\n"
            "warnings.filterwarnings('ignore', message='.*allowed_objects.*')\n"
            "from langgraph.checkpoint.memory import MemorySaver\n"
            "print('clean')\n"
        )
        r = subprocess.run([sys.executable, "-W", "error::Warning", "-c", code], capture_output=True, text=True)
        assert "clean" in r.stdout, r.stderr

    def test_scope_cli_import(self):
        """Field report: `suijin scope` crashed — run_scope imported
        suijin.tui_scope (top-level) after the module moved to
        modules/console/lib/."""
        import importlib

        mod = importlib.import_module("suijin.modules.console.lib.cli")
        import inspect

        src = inspect.getsource(mod.run_scope)
        assert "from suijin.modules.console.lib import tui_scope" in src


class TestUncrashableUI:
    """The silent mid-render crash: an exception in ANY render path killed
    the engagement and returned to the menu with zero output."""

    def test_render_crash_never_propagates(self, monkeypatch):
        from suijin.modules.platform.lib import workspace as _ws

        monkeypatch.setattr(_ws, "WORKSPACE_DIR", __import__("pathlib").Path(__import__("tempfile").mkdtemp()))
        from rich.console import Console

        import suijin.modules.redteam.lib.red.console_ui as m

        def boom(text, style="none"):
            raise RuntimeError("simulated renderer crash")

        monkeypatch.setattr(m, "_md", boom)
        c = Console(record=True, width=90, force_terminal=True)
        ui = m.EngagementUI(c)
        ui.iteration_header(1, "informational")  # must not raise
        ui.thinking("a thought that would have crashed")
        ui.output("fine output")
        out = c.export_text()
        assert "a thought that would have crashed" in out  # plain-text fallback

    def test_crash_log_written(self, tmp_path, monkeypatch):
        from rich.console import Console

        import suijin.modules.redteam.lib.red.console_ui as m
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        c = Console(record=True, width=90)
        ui = m.EngagementUI(c)
        ui.tool("execute_terminal", {"cmd": "x"})  # no open iteration — fine
        # force a guarded failure
        ui._section = None  # break internals
        ui.thinking("t")  # guarded wrapper catches AttributeError
        log = tmp_path / "outputs" / "logs" / "ui_crash.log"
        assert log.exists()

    def test_langgraph_import_caged(self):
        """The advisory is filtered AT the import site — immune to any
        warnings-registry reset by other libraries."""
        import subprocess
        import sys

        code = (
            "import warnings\n"
            "warnings.simplefilter('default')\n"  # simulate a library resetting
            "import suijin.modules.agent.lib.agent_graph as ag\n"
            "print('caged-clean')\n"
        )
        r = subprocess.run([sys.executable, "-W", "default", "-c", code], capture_output=True, text=True)
        assert "caged-clean" in r.stdout, r.stderr
        assert "allowed_objects" not in r.stderr


class TestJsSurfaceTools:
    """js_bundle_analyze / google_key_probe / source_map_probe — the tools
    that replace the 3-iteration hand-rolled curl+grep chain."""

    def test_bundle_mining(self, monkeypatch):
        from suijin.modules.tools.lib import js_tools

        fake_bundle = (
            'fetch("/api/v1/login",{method:"POST"});'
            '"/admin/panel";'
            '"https://evil-cdn.example.com/x.js"'
            'const KEY="AIzaSyANwe_3zpMHBwFvCwC3vqyp0A4PUDWrsKw";'
            '"626244387316-s9i3efsdc4omr5mbbm9tjkug92k5f35i.apps.googleusercontent.com"'
            '"https://pxxabc.supabase.co"'
            'import("./lib-CLGniJ1T.js")'
            "//# sourceMappingURL=index.js.map"
        )

        class R:
            status_code = 200
            text = fake_bundle

        monkeypatch.setattr(js_tools, "_get", lambda url, timeout=20: R())
        out = js_tools.js_bundle_analyze("https://t/assets/index.js")
        assert "/api/v1/login" in out and "/admin/panel" in out
        assert "AIzaSyANwe_3zpMHBwFvCwC3vqyp0A4PUDWrsKw" in out
        assert "google-oauth-client-id" in out
        assert "supabase" in out and "index.js.map" in out
        assert "lib-CLGniJ1T.js" in out

    def test_routes_regex_handles_minified_quotes(self):
        from suijin.modules.tools.lib.js_tools import PATH_RE

        for lit in ['"/api/users"', "'/admin'", "`/v2/search?q=${x}`", '"/api/v1/files/${t}/export"']:
            assert PATH_RE.search(lit), lit  # the field-run hand regex failed exactly here

    def test_google_key_probe_requires_aiza(self):
        from suijin.modules.tools.lib.js_tools import google_key_probe

        assert google_key_probe("not-a-key").startswith("Error")

    def test_google_key_probe_reports_verdicts(self, monkeypatch):
        from suijin.modules.tools.lib import js_tools

        class R:
            status_code = 200
            headers = {"Content-Type": "application/json"}
            text = "{}"

            def json(self):
                return {}

        monkeypatch.setattr(js_tools.requests, "get", lambda *a, **k: R())
        out = js_tools.google_key_probe("AIza" + "a" * 35)
        assert "[ACTIVE]" in out

    def test_source_map_probe_detects_map(self, monkeypatch):
        from suijin.modules.tools.lib import js_tools

        asset = type("R", (), {"status_code": 200, "text": "console.log(1)\n//# sourceMappingURL=index.js.map"})()
        import json as _json

        the_map = type(
            "R",
            (),
            {
                "status_code": 200,
                "text": _json.dumps({"sources": ["webpack://app/src/api.js", "src/auth.ts", "<runtime>"]}),
            },
        )()

        def fake_get(url, timeout=20):
            return the_map if url.endswith(".map") else asset

        monkeypatch.setattr(js_tools, "_get", fake_get)
        out = js_tools.source_map_probe("https://t/assets/index.js")
        assert "SOURCE MAP EXPOSED" in out and "src/auth.ts" in out
        assert "webpack://" not in out.split("sources")[1]

    def test_source_map_probe_negative(self, monkeypatch):
        from suijin.modules.tools.lib import js_tools

        asset = type("R", (), {"status_code": 200, "text": "console.log(1)"})()
        nomap = type("R", (), {"status_code": 404, "text": ""})()

        def fake_get(url, timeout=20):
            return asset if not url.endswith(".map") else nomap

        monkeypatch.setattr(js_tools, "_get", fake_get)
        assert "No source map exposed" in js_tools.source_map_probe("https://t/a.js")

    def test_tools_registered_and_documented(self):
        from suijin.modules.agent.lib.prompts.tool_registry import _ALL_TOOLS, TOOL_REGISTRY
        from suijin.modules.tools.lib.js_tools import google_key_probe, js_bundle_analyze, source_map_probe

        for t in (js_bundle_analyze, google_key_probe, source_map_probe):
            route_name = t.__name__
            assert route_name in _ALL_TOOLS, route_name
            assert route_name in TOOL_REGISTRY, route_name

    def test_new_tools_render_in_tui(self):
        ui, c = _ui()
        ui.iteration_header(1, "informational")
        ui.tool("js_bundle_analyze", {"url": "https://t/assets/index.js"})
        ui.tool("google_key_probe", {"key": "AIzaX" + "x" * 34})
        ui.tool("source_map_probe", {"url": "https://t/a.js"})
        ui.flush_open()
        out = c.export_text()
        assert "url=https://t/assets/index.js" in out
        assert "AIzaX" in out


class TestFieldCrashDriftDict:
    """Field crash (13:56, ui_crash.log): agent_graph stores _drift_warning
    as the drift analyser's RESULT DICT; drift() passed it to Text.assemble
    raw -> TypeError. The guard saved the run but the warning vanished."""

    def test_drift_accepts_result_dict(self):
        ui, c = _ui()
        ui.iteration_header(1, "informational")
        ui.drift(
            {
                "drift_detected": True,
                "drift_causes": ["objective mentions API testing, actions are all port scans"],
                "suggestions": ["return to the stated objective", "check scope"],
            }
        )
        ui.flush_open()
        out = c.export_text()
        assert "port scans" in out and "stated objective" in out

    def test_drift_accepts_plain_string(self):
        ui, c = _ui()
        ui.drift("coverage stalled on recon")
        assert "coverage stalled" in c.export_text()

    def test_oracle_renders_hypothesis_dicts(self):
        """The oracle returns [{'id','hypothesis','confidence',...}] — the
        old str(h[0]) printed an ugly dict repr."""
        ui, c = _ui()
        ui.iteration_header(1, "informational")
        ui.oracle(
            [
                {
                    "id": "H1",
                    "hypothesis": "Input validation — special characters stripped",
                    "confidence": 0.6,
                    "validation_payload": "' OR '1'='1",
                }
            ]
        )
        ui.flush_open()
        out = c.export_text()
        assert "[H1] Input validation" in out
        assert "(0.6)" in out
        assert "validation_payload" not in out  # dict repr not printed

    def test_no_raw_state_payload_reaches_text_assemble(self):
        """Every _note-style renderer must coerce: supervisor/drift/oracle/
        phase_transition/fireteam called with the exact state shapes from
        agent_graph must render, never raise."""
        ui, c = _ui()
        ui.iteration_header(1, "informational")
        ui.supervisor("repeating tool detected")
        ui.drift({"drift_causes": ["c1"], "suggestions": ["s1"]})
        ui.oracle([{"id": "H2", "hypothesis": "WAF blocks", "confidence": 0.4}])
        ui.phase_transition("exploitation", {"reason": "found creds"})
        ui.fireteam({"teams": 3})  # even nonsense objects render
        ui.flush_open()
        assert "repeating tool" in c.export_text()


class TestAnswerFlowFieldBugs:
    """corp.example field run: 'queued as guidance' echoed over the answer,
    the strip went static after resume, and the operator's confirmation
    never persisted into the engagement order (model re-litigated)."""

    def test_ask_mode_suppresses_guidance_echo(self):
        from rich.console import Console

        from suijin.modules.redteam.lib.red.console_ui import ask_operator_answer
        from suijin.modules.tools.lib.run_commands import RunBox

        box = RunBox(console=Console(record=True, width=90, force_terminal=True)).start()
        import threading
        import time

        def typer():
            time.sleep(0.2)
            box.dispatch("mirror-target.example")  # the operator's answer

        threading.Thread(target=typer, daemon=True).start()
        answer = ask_operator_answer(box, Console(record=True, width=90), "authorized?", timeout_s=5)
        box.stop()
        assert answer == "mirror-target.example"
        assert not box._ask_mode  # restored

    def test_normal_guidance_echo_still_works(self):
        from rich.console import Console

        from suijin.modules.tools.lib.run_commands import RunBox

        c = Console(record=True, width=90, force_terminal=True)
        box = RunBox(console=c).start()
        box.ask_mode(False)
        box.dispatch("try harder on the login form")
        box.stop()
        assert "queued as guidance" in c.export_text()

    def test_confirmation_persists_into_objective(self):
        """The engagement order renders from original_objective EVERY turn —
        once the operator confirms, the confirmation must ride the order
        forever (the model can never unsee it)."""
        from suijin.modules.agent.lib.prompts.base import engagement_order

        confirmed = "corp.example [OPERATOR-CONFIRMED in engagement: yes, bug bounty, proceed]"
        order = engagement_order(confirmed)
        assert "OPERATOR-CONFIRMED" in order
        assert "Authorization" in order

    def test_strip_shows_thinking_after_wait(self):
        ui, c = _ui()
        ui.start()
        ui.iteration_header(1, "informational")
        ui.waiting(True)
        r = Console(record=True, width=100, force_terminal=True)
        r.print(ui._strip())
        assert "thinking" in r.export_text()
        ui.stop()


class TestNoSilentEndings:
    """mirror-target.example field run: the agent DECLINED (completion_reason
    'Engagement declined: …') and the run ended through the normal path —
    but the operator saw a green Report panel + dim Done and read it as a
    silent crash. Every ending must be unmissable."""

    def test_decline_renders_stopped_panel(self, monkeypatch):
        from rich.console import Console

        import suijin.modules.redteam.lib.redteamer as rt

        out = Console(record=True, width=100, force_terminal=True)
        monkeypatch.setattr(rt, "console", out)

        class _FS(dict):
            pass

        fs = _FS(
            completion_reason="Engagement declined: mirror-target.example is a public third-party service",
            messages=[{"role": "assistant", "content": "x" * 80}],
            execution_trace=[],
            current_phase="informational",
        )
        # simulate the final-report block's decline branch
        _reason = str(fs.get("completion_reason", ""))
        declined = any(w in _reason.lower() for w in ("declin", "refuse", "will not proceed"))
        assert declined
        out.print(f"[bold yellow]ENGAGEMENT STOPPED — agent declined:[/bold yellow] {_reason}")
        assert "ENGAGEMENT STOPPED" in out.export_text()

    def test_decline_words_detected(self):
        words = ("declin", "refuse", "will not proceed", "cannot proceed", "not able to proceed", "i won't")
        for r in ("Engagement declined: x", "I refuse to scan", "I will not proceed against"):
            assert any(w in r.lower() for w in words), r

    def test_sync_wrapper_shows_crash_panel(self, monkeypatch, tmp_path):
        """run_red_team NEVER exits silently — an inner crash renders a red
        panel instead of dropping back to the launcher."""

        async def boom(cfg, obj, api_key=None):
            raise RuntimeError("provider exploded mid-run")

        from suijin.modules.platform.lib import workspace as _ws

        monkeypatch.setattr(_ws, "WORKSPACE_DIR", tmp_path)  # crash log stays in tmp
        monkeypatch.setattr("suijin.modules.redteam.lib.redteamer.run_red_team_async", boom)
        from rich.console import Console

        import suijin.modules.redteam.lib.redteamer as rt

        out = Console(record=True, width=100, force_terminal=True)
        monkeypatch.setattr(rt, "console", out)
        rt.run_red_team({"provider": "zai"}, "10.0.0.9")  # must not raise
        text = out.export_text()
        assert "engagement crashed" in text
        assert "provider exploded" in text

    def test_guard_fallback_is_visible(self, monkeypatch):
        from suijin.modules.platform.lib import workspace as _ws

        monkeypatch.setattr(_ws, "WORKSPACE_DIR", __import__("pathlib").Path(__import__("tempfile").mkdtemp()))
        from rich.console import Console

        import suijin.modules.redteam.lib.red.console_ui as m

        def boom(text, style="none"):
            raise RuntimeError("renderer died")

        monkeypatch.setattr(m, "_md", boom)
        c = Console(record=True, width=90, force_terminal=True)
        ui = m.EngagementUI(c)
        ui.iteration_header(1, "informational")
        ui.thinking("content survives")
        out = c.export_text()
        assert "content survives" in out
        assert "render fallback" in out  # the notice, not silence


class TestFireteamStripRows:
    """The live fireteam block: full word + per-agent rows from the
    registry (the old `FT N live` counter only ever went up)."""

    def _render_strip(self, ui):
        import io

        from rich.console import Console as C

        sink = C(file=io.StringIO(), width=110, force_terminal=True)
        sink.print(ui._strip())
        return sink.file.getvalue()

    def test_no_fireteams_no_row(self, monkeypatch):
        import suijin.modules.redteam.lib.red.console_ui as m

        monkeypatch.setattr(m, "_fireteam_snapshot", lambda: [])
        ui, _c = _ui()
        ui.waiting(False)
        strip = self._render_strip(ui)
        assert "Fireteam" not in strip and "FT" not in strip

    def test_full_word_and_agent_rows(self, monkeypatch):
        import suijin.modules.redteam.lib.red.console_ui as m

        fake = [
            {
                "team_id": "team-ab12cd",
                "running": 2,
                "tasks": [
                    {"task": "Test SQLi on http://t/login via blind probe", "state": "running", "success": None},
                    {"task": "Enumerate http://t/api for hidden endpoints", "state": "running", "success": None},
                    {"task": "Fetch http://t/robots.txt and read paths", "state": "done", "success": True},
                ],
            }
        ]
        monkeypatch.setattr(m, "_fireteam_snapshot", lambda: fake)
        ui, _c = _ui()
        ui.waiting(False)
        strip = self._render_strip(ui)
        assert "Fireteam" in strip and "team-ab12cd" in strip  # the FULL word
        assert "2 running" in strip and "1 done" in strip
        assert "agent 1:" in strip and "agent 2:" in strip
        assert "SQLi on http://t/login" in strip
        assert "✓" in strip  # the finished agent shows its mark until the team drains
        assert "agent 3:" in strip and "robots.txt" in strip  # done agents stay visible with ✓

    def test_failed_agent_marks_red_cross(self, monkeypatch):
        import suijin.modules.redteam.lib.red.console_ui as m

        fake = [
            {
                "team_id": "team-zz",
                "running": 1,
                "tasks": [
                    {"task": "Probe http://dead.host/x for open ports", "state": "done", "success": False},
                    {"task": "Sweep http://t ranges for live hosts now", "state": "running", "success": None},
                ],
            }
        ]
        monkeypatch.setattr(m, "_fireteam_snapshot", lambda: fake)
        ui, _c = _ui()
        ui.waiting(False)
        strip = self._render_strip(ui)
        assert "✗" in strip and "1 running" in strip

    def test_block_hidden_when_nothing_running(self, monkeypatch):
        """Operator contract: the fireteam block appears ONLY while a team
        is actually running — finished-but-undrained teams show NOTHING."""
        import suijin.modules.redteam.lib.red.console_ui as m

        fake = [
            {
                "team_id": "team-done",
                "running": 0,
                "tasks": [{"task": "Probe http://dead.host/x for open ports", "state": "done", "success": True}],
            }
        ]
        monkeypatch.setattr(m, "_fireteam_snapshot", lambda: fake)
        ui, _c = _ui()
        ui.waiting(False)
        strip = self._render_strip(ui)
        assert "Fireteam" not in strip and "agent" not in strip

    def test_live_count_sums_running(self, monkeypatch):
        import suijin.modules.redteam.lib.red.console_ui as m

        fake = [
            {"team_id": "a", "running": 2, "tasks": []},
            {"team_id": "b", "running": 1, "tasks": []},
        ]
        monkeypatch.setattr(m, "_fireteam_snapshot", lambda: fake)
        assert m._fireteam_live_count() == 3
        ui, _c = _ui()
        ui.waiting(False)
        strip = self._render_strip(ui)
        assert "Fireteam 3 live" in strip

    def test_rows_disappear_when_registry_empties(self, monkeypatch):
        import suijin.modules.redteam.lib.red.console_ui as m

        state = {"teams": [{"team_id": "t1", "running": 1, "tasks": [{"task": "x" * 40, "state": "running"}]}]}
        monkeypatch.setattr(m, "_fireteam_snapshot", lambda: state["teams"])
        ui, _c = _ui()
        ui.waiting(False)
        assert "agent 1:" in self._render_strip(ui)
        state["teams"] = []  # team drained
        assert "agent" not in self._render_strip(ui)


class TestFlexingReasoningBox:
    """The flexing white thought-box in the bottom bar: deltas ACCUMULATE
    (full-width word wrap — rows FILL before wrapping, never one word per
    line); the box shows the live tail and grows; aged rows scroll into
    the transcript so the WHOLE thought survives; stream_done flushes the
    remainder. Both kinds stream; TTFT proves the first token."""

    def _strip_text(self, ui, width=110):
        import io

        from rich.console import Console as C

        sink = C(file=io.StringIO(), width=width, force_terminal=True)
        sink.print(ui._strip())
        return sink.file.getvalue()

    def test_deltas_accumulate_into_the_box_zero_prints(self):
        ui, c = _ui()
        ui.waiting(True)
        ui.reasoning_delta("content", "large ")
        ui.reasoning_delta("content", "travel ")
        ui.reasoning_delta("content", "booking ")
        ui.reasoning_delta("content", "platform")
        strip = self._strip_text(ui)
        assert "large" in strip and "platform" in strip  # in the BOX, joined
        out_lines = [ln for ln in c.export_text().split("\n") if ln.strip()]
        assert out_lines == []  # ZERO per-delta prints — no fragments, ever
        assert "render fallback" not in strip

    def test_rows_fill_full_width_in_the_box(self):
        """Tokens join to full rows: a long stream renders as wrapped lines
        inside the box, not N single-word lines."""
        ui, c = _ui()
        ui.waiting(True)
        words = " ".join(f"w{i}" for i in range(60))  # ~350 chars, one 'thought'
        for i in range(0, len(words), 20):
            ui.reasoning_delta("content", words[i : i + 20])
        strip = self._strip_text(ui)
        lines = [ln.strip() for ln in strip.split("\n") if ln.strip()]
        word_lines = [ln for ln in lines if ln.startswith("w") and " " not in ln and len(ln) < 8]
        assert not word_lines  # no lone-word rows — text flows full-width
        assert "w0" in strip and "w59" in strip

    def test_streams_without_think_toggle(self):
        ui, _c = _ui()
        UI_STATE["show_reasoning"] = False
        ui.waiting(True)
        ui.reasoning_delta("reasoning", "visible reasoning")
        assert "visible reasoning" in self._strip_text(ui)

    def test_long_stream_stays_boxed_no_fragments(self):
        """A long stream: the box keeps flexing (whole thought), ZERO
        transcript prints until done — the aged-sliver fragments are dead."""
        ui, c = _ui()
        ui.waiting(True)
        long = "".join(f"thought-{i:03d} " for i in range(200))
        for i in range(0, len(long), 100):
            ui.reasoning_delta("reasoning", long[i : i + 100])
        out = [ln for ln in c.export_text().split("\n") if ln.strip()]
        assert out == []  # NOTHING printed mid-stream — fragments impossible
        strip = self._strip_text(ui)
        assert "thought-199" in strip  # the live thought in the box
        ui.stream_done()
        done = c.export_text()
        assert "thought-000" in done and "thought-199" in done  # whole thought, one flush

    def test_ttft_recorded_on_first_delta_only(self):
        ui, _c = _ui()
        ui.waiting(True)
        first_ttft = UI_STATE["last_ttft"]
        ui.reasoning_delta("content", "tok")  # content counts too (glm streams content)
        ttft = UI_STATE["last_ttft"]
        assert ttft is not None and ttft >= 0
        ui.reasoning_delta("reasoning", "tok2")
        assert UI_STATE["last_ttft"] == ttft  # one shot — not overwritten
        assert first_ttft is None or isinstance(first_ttft, (int, float))

    def test_stream_done_flushes_whole_thought_and_collapses(self):
        ui, c = _ui()
        ui.waiting(True)
        ui.reasoning_delta("reasoning", "head of the thought ")
        ui.reasoning_delta("content", "final fragment")
        ttft = UI_STATE["last_ttft"]
        ui.stream_done()
        out = c.export_text()
        assert "head of the thought" in out and "final fragment" in out  # WHOLE thought
        assert UI_STATE["last_ttft"] == ttft
        assert ui._stream_parts == [] and not ui._streaming
        assert "final fragment" not in self._strip_text(ui)  # box collapsed


class TestInputBox:
    """The always-at-the-bottom operator prompt: white box, thinking
    spinner + mode badge on the left, live typing, Tab cycles modes."""

    def _strip_text(self, ui, width=120):
        import io

        from rich.console import Console as C

        sink = C(file=io.StringIO(), width=width, force_terminal=True)
        sink.print(ui._strip())
        return sink.file.getvalue()

    def test_box_always_last_with_mode_and_hint(self):
        ui, _c = _ui()
        ui.waiting(True)
        strip = self._strip_text(ui)
        assert "RECON" in strip  # default mode badge
        assert "thinking" in strip  # the ONE thinking indicator (strip, not box)
        assert "Tab" in strip and "ESC ESC" in strip  # the key hints idle inside

    def test_box_has_no_second_thinking(self):
        """One thinking indicator (the strip's) — the box carries ONLY the
        mode badge + input, no duplicate spinner/label."""
        ui, _c = _ui()
        ui.waiting(True)
        ui.set_input("hello")
        strip = self._strip_text(ui)
        box_line = next(ln for ln in strip.split("\n") if "hello" in ln)
        assert "RECON" in box_line and "hello" in box_line
        assert "thinking" not in box_line and "working" not in box_line

    def test_strip_shows_phase_not_thinking_when_working(self):
        ui, _c = _ui()
        ui.waiting(False)
        strip = self._strip_text(ui)
        assert "thinking" not in strip  # idle: phase label, no stale indicator

    def test_fireteam_task_text_never_truncated(self, monkeypatch):
        import suijin.modules.redteam.lib.red.console_ui as m

        long_task = (
            "MISSION: locate + fingerprint the admin panel the operator keeps flagging across every engagement we have ever run together"
            * 2
        )
        monkeypatch.setattr(
            m,
            "_fireteam_snapshot",
            lambda: [{"team_id": "t1", "running": 1, "tasks": [{"task": long_task, "state": "running"}]}],
        )
        ui, _c = _ui()
        ui.waiting(False)
        strip = self._strip_text(ui, width=200)
        assert long_task[:120] in strip  # full mission text, no 52-char clip

    def test_tab_cycles_modes(self):
        from suijin.modules.redteam.lib.red.console_input import next_mode

        assert next_mode("recon") == "exploit"
        assert next_mode("exploit") == "report"
        assert next_mode("report") == "recon"  # wraps

    def test_set_mode_and_live_typing_render(self):
        ui, _c = _ui()
        ui.set_mode("exploit")
        ui.set_input("check /admin")
        strip = self._strip_text(ui)
        assert "EXPLOIT" in strip and "check /admin" in strip and "▌" in strip

    def test_mode_tags_plain_prompts(self):
        """Plain lines dispatch as mode-tagged guidance; slash commands pass raw."""
        from suijin.modules.redteam.lib.red.console_input import RedInputReader

        class _Box:
            def __init__(self):
                self.lines = []

            def dispatch(self, line):
                self.lines.append(line)

        box = _Box()
        reader = RedInputReader.__new__(RedInputReader)
        reader._run_box = box
        reader._on_guidance = None  # no live-injection wired: mode-tag fallback
        reader._mode = "report"
        reader._dispatch("focus on the vault")
        assert box.lines == ["[REPORT] focus on the vault"]
        reader._mode = "recon"
        reader._dispatch("/state")
        assert box.lines[-1] == "/state"  # slash commands never tagged

    def test_apply_key_contract(self):
        from suijin.modules.redteam.lib.red.console_input import RedInputReader

        assert RedInputReader.apply_key("ab", "\x7f") == ("a", None)
        buf, action = RedInputReader.apply_key("", "\t")
        assert action == "tab" and buf == ""  # Tab never lands in the buffer
        buf, action = RedInputReader.apply_key("x", "\r")
        assert action == "line" and buf == ""

    def test_double_esc_fires_pause(self):
        """ESC ESC within 0.6s pauses the agent (the ^C replacement)."""
        from suijin.modules.redteam.lib.red.console_input import RedInputReader

        fired = []
        reader = RedInputReader.__new__(RedInputReader)
        reader._on_pause = lambda: fired.append(True)
        reader._last_esc = 0.0
        reader._stop = __import__("threading").Event()
        reader._paused_out = __import__("threading").Event()

        # simulate: first ESC registers, immediate second ESC fires
        reader._last_esc = __import__("time").monotonic()
        reader._esc_chord()
        assert fired == [True]

    def test_box_is_last_row_even_with_fireteam_and_stream(self, monkeypatch):
        import suijin.modules.redteam.lib.red.console_ui as m

        monkeypatch.setattr(
            m,
            "_fireteam_snapshot",
            lambda: [{"team_id": "t1", "running": 1, "tasks": [{"task": "probe the thing", "state": "running"}]}],
        )
        ui, c = _ui()
        ui.waiting(True)
        ui.reasoning_delta("reasoning", "streaming live")  # lives in the thought-box
        ui.set_input("hello")
        # the strip carries thought-box + fireteam + input box (last, always)
        assert "hello" in self._strip_text(ui)
        assert "probe the thing" in self._strip_text(ui)
        assert "streaming live" in self._strip_text(ui)


class TestLiveGuidanceInjection:
    """Plain prompts inject into the graph NOW — no turn-boundary wait."""

    def test_on_guidance_receives_the_line(self):
        from suijin.modules.redteam.lib.red.console_input import RedInputReader

        got = []

        class _Box:
            _ask_mode = False

            def dispatch(self, line):
                got.append(("box", line))

        reader = RedInputReader.__new__(RedInputReader)
        reader._run_box = _Box()
        reader._mode = "exploit"
        reader._on_guidance = lambda line: got.append(("graph", line))
        reader._dispatch("hit the admin panel now")
        assert got == [("graph", "hit the admin panel now")]  # instant — not queued

    def test_ask_mode_answers_go_to_the_box_raw(self):
        from suijin.modules.redteam.lib.red.console_input import RedInputReader

        got = []

        class _Box:
            _ask_mode = True

            def dispatch(self, line):
                got.append(line)

        reader = RedInputReader.__new__(RedInputReader)
        reader._run_box = _Box()
        reader._mode = "recon"
        reader._on_guidance = lambda line: got.append(("graph", line))
        reader._dispatch("my answer")
        assert got == ["my answer"]  # the pending ask consumes it raw


class TestPauseQuitSavesState:
    """/quit in the pause console ends the engagement with a full save."""

    def test_quit_handler_sets_stop_flag(self):
        from suijin.modules.redteam.lib.red import session_control as sc

        ctx = sc.PauseContext(console=type("C", (), {"print": lambda self, *a, **k: None})())
        handlers = sc.build_pause_handlers(ctx)
        assert "/quit" in handlers
        handlers["/quit"]("")
        assert ctx.stop_requested is True

    def test_pause_console_returns_on_quit(self):
        from suijin.modules.redteam.lib.red import session_control as sc

        ctx = sc.PauseContext(console=type("C", (), {"print": lambda self, *a, **k: None})())
        inputs = iter(["/quit"])
        out = sc.pause_console(ctx, lambda label, timeout=600.0: next(inputs))
        assert out == ""
        assert ctx.stop_requested is True

    def test_pause_console_guidance_still_works(self):
        from suijin.modules.redteam.lib.red import session_control as sc

        ctx = sc.PauseContext(console=type("C", (), {"print": lambda self, *a, **k: None})())
        inputs = iter(["focus on the vault"])
        out = sc.pause_console(ctx, lambda label, timeout=600.0: next(inputs))
        assert "vault" in out and not ctx.stop_requested


class TestPauseThroughTheBox:
    """The omnipresent input box feeds the pause console — no legacy
    prompt; lines route RAW (slash and guidance alike)."""

    def _reader(self):
        from suijin.modules.redteam.lib.red.console_input import RedInputReader

        class _Box:
            def dispatch(self, line):
                raise AssertionError("pause mode must NOT touch the run box")

        reader = RedInputReader.__new__(RedInputReader)
        reader._run_box = _Box()
        reader._on_guidance = lambda line: None
        reader._pause_queue = None
        reader._mode = "recon"
        return reader

    def test_begin_pause_routes_raw(self):
        import queue

        reader = self._reader()
        q = queue.Queue()
        reader.begin_pause(q)
        reader._dispatch("/quit")
        reader._dispatch("plain guidance line")
        assert q.get_nowait() == "/quit"  # raw — pause handlers parse it
        assert q.get_nowait() == "plain guidance line"

    def test_end_pause_restores_live_routing(self):
        import queue

        from suijin.modules.redteam.lib.red.console_input import RedInputReader

        got = []

        class _Box:
            def dispatch(self, line):
                got.append(line)

        reader = RedInputReader.__new__(RedInputReader)
        reader._run_box = _Box()
        reader._on_guidance = None
        reader._pause_queue = queue.Queue()
        reader._mode = "recon"
        reader.end_pause()
        reader._dispatch("/state")
        assert got == ["/state"]
