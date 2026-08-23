"""H1 — the engagement state board: harvest, merge, render, honest growth."""

import asyncio
import json


class TestExtractors:
    def test_nmap_ports_services(self):
        from suijin.modules.agent.lib.target_board import extract_from_output

        out = "PORT      STATE SERVICE VERSION\n22/tcp    open  ssh     OpenSSH 8.4p1\n443/tcp   open  https   nginx 1.18.0"
        upd = extract_from_output("execute_terminal", {"cmd": "nmap -sV"}, out)
        assert upd["ports"] == [22, 443]
        assert any("nginx" in s for s in upd["services"])

    def test_nmap_does_not_eat_closed_ports(self):
        from suijin.modules.agent.lib.target_board import extract_from_output

        out = "22/tcp  closed ssh\n443/tcp open  https"
        upd = extract_from_output("nmap", {}, out)
        assert 443 in upd["ports"] and 22 not in upd["ports"]

    def test_http_tech_from_headers(self):
        from suijin.modules.agent.lib.target_board import extract_from_output

        out = "Status: 200\nHeaders: {'Server': 'cloudflare', 'X-Powered-By': 'PHP/7.4'}"
        upd = extract_from_output("http_request", {"url": "https://t.co/x"}, out)
        assert any("cloudflare" in t.lower() for t in upd["technologies"])
        assert any("PHP" in t for t in upd["technologies"])

    def test_endpoint_recorded_from_url(self):
        from suijin.modules.agent.lib.target_board import extract_from_output

        upd = extract_from_output("http_request", {"url": "https://api.t.co/v1/users?page=2"}, "Status: 404")
        assert any("api.t.co/v1/users" in e for e in upd["endpoints"])

    def test_bundle_routes(self):
        from suijin.modules.agent.lib.target_board import extract_from_output

        bundle = "== ROUTES (2) ==\n  /api/login\n  /admin/panel"
        upd = extract_from_output("js_bundle_analyze", {"url": "https://t.co/a.js"}, bundle)
        assert "/api/login" in upd["endpoints"] and "/admin/panel" in upd["endpoints"]

    def test_credentials_harvested(self):
        from suijin.modules.agent.lib.target_board import extract_from_output

        upd = extract_from_output("http_request", {}, "leak: AKIAIOSFODNN7EXAMPLE")
        assert upd["credentials"][0]["kind"] == "AWS key"

    def test_nothing_from_noise(self):
        from suijin.modules.agent.lib.target_board import extract_from_output

        assert extract_from_output("http_request", {"url": "https://t.co/"}, "Status: 200\nBody:\nhello") == {}


class TestMerge:
    def test_merge_dedupe_and_grew(self):
        from suijin.modules.agent.lib.target_board import merge_updates

        b1, g1 = merge_updates({}, {"ports": [80, 443]})
        assert g1 and b1["ports"] == [80, 443]
        b2, g2 = merge_updates(b1, {"ports": [443, 8080]})
        assert g2 and b2["ports"] == [80, 443, 8080]
        _, g3 = merge_updates(b2, {"ports": [80, 443, 8080]})
        assert not g3  # idempotent — no growth signal without new intel

    def test_creds_merge_by_value(self):
        from suijin.modules.agent.lib.target_board import merge_updates

        b, _ = merge_updates({}, {"credentials": [{"kind": "k", "value": "v1"}]})
        b, g = merge_updates(b, {"credentials": [{"kind": "k", "value": "v1"}, {"kind": "k", "value": "v2"}]})
        assert g and len(b["credentials"]) == 2

    def test_caps(self):
        from suijin.modules.agent.lib.target_board import _CAP, merge_updates

        b, _ = merge_updates({}, {"ports": list(range(500))})
        assert len(b["ports"]) == _CAP


class TestRender:
    def test_board_renders_sections(self):
        from suijin.modules.agent.lib.target_board import render_board

        r = render_board(
            {"ports": [22, 443], "endpoints": ["/a", "/b"], "credentials": [{"kind": "AWS key", "value": "AKIA…"}]},
            {"sqli": {"attempts": 3, "failures": 1}},
            ["j1", "j2"],
        )
        assert "ports (2)" in r and "endpoints (2)" in r and "sqli(3a/1f)" in r
        assert "RUNNING background jobs: j1, j2" in r

    def test_empty_board_honest(self):
        from suijin.modules.agent.lib.target_board import render_board

        assert "nothing recorded yet" in render_board({})


class TestExecuteSeam:
    def test_successful_tool_merges_board_and_sets_grew(self):
        """The wiring: a successful nmap result must populate target_info
        and set the honest growth flag the stall counter reads."""
        from suijin.modules.agent.lib.nodes.execute_tool_node import execute_tool_node

        nmap_out = (
            "Starting Nmap\nPORT   STATE SERVICE VERSION\n"
            "22/tcp open  ssh     OpenSSH 8.9\n443/tcp open  https nginx 1.20"
        )
        out = asyncio.run(
            execute_tool_node(
                {
                    "_current_step": {
                        "tool_name": "execute_terminal",
                        "tool_args": {"cmd": "nmap -sV"},
                        "iteration": 1,
                        "phase": "informational",
                    },
                    "current_phase": "informational",
                },
                route_tool_fn=lambda n, a, c: nmap_out,
            )
        )
        assert out["_target_grew_last_step"] is True
        assert 443 in out["target_info"]["ports"]
        assert any("nginx" in s for s in out["target_info"]["services"])

    def test_failed_tool_leaves_board_alone(self):
        from suijin.modules.agent.lib.nodes.execute_tool_node import execute_tool_node

        out = asyncio.run(
            execute_tool_node(
                {
                    "_current_step": {
                        "tool_name": "http_request",
                        "tool_args": {"url": "https://dead.invalid"},
                        "iteration": 1,
                    },
                    "current_phase": "informational",
                },
                route_tool_fn=lambda n, a, c: "Error: connection refused",
            )
        )
        assert out["_target_grew_last_step"] is False
        assert "target_info" not in out


class TestThinkRendersBoard:
    def test_context_block_carries_the_board(self):
        from suijin.modules.agent.lib.nodes import think_node as tn

        captured = {}

        async def gen(messages, config=None, **kw):
            captured["system"] = messages[0]["content"]
            return json.dumps({"action": "complete", "completion_reason": "done", "thought": "t"})

        asyncio.run(
            tn.think_node(
                {
                    "messages": [],
                    "execution_trace": [],
                    "current_iteration": 2,
                    "current_phase": "informational",
                    "original_objective": "10.0.0.9",
                    "todo_list": [],
                    "target_info": {"ports": [22, 80], "endpoints": ["/api"]},
                    "tested_axes": {"sqli": {"attempts": 2, "failures": 0}},
                },
                generate_fn=gen,
            )
        )
        sys_prompt = captured["system"]
        assert "TARGET INTELLIGENCE (your working board" in sys_prompt
        assert "ports (2)" in sys_prompt and "endpoints (1)" in sys_prompt
        assert "sqli(2a/0f)" in sys_prompt

    def test_honest_growth_flag_consumed(self):
        """state_grew now reads the execute-set flag instead of comparing a
        dict with itself (the always-False bug)."""
        import inspect

        from suijin.modules.agent.lib.nodes import think_node as tn

        src = inspect.getsource(tn.think_node)
        assert "_target_grew_last_step" in src
        assert "will compare after tool runs" not in src  # the fake comment is gone


class TestH2JobSemantics:
    """Results stop vanishing: finished jobs drain into the conversation
    (fireteam symmetry), waits never self-background, status untruncated."""

    def _mkjob(self, monkeypatch, tmp_path, jid="j1", status="done", output="x" * 2000, announce=True):
        import suijin.modules.tools.lib.job_registry as jr

        monkeypatch.setattr(
            jr,
            "_jobs",
            {
                jid: {
                    "job_id": jid,
                    "tool_name": "nmap",
                    "tool_args": {},
                    "status": status,
                    "started_at": 1.0,
                    "output": output,
                    "error": None,
                    "_announce": announce,
                }
            },
        )
        monkeypatch.setattr(jr, "_drained", set())
        return jr

    def test_finished_job_drains_once(self, monkeypatch, tmp_path):
        jr = self._mkjob(monkeypatch, tmp_path)
        msgs = jr.collect_finished_jobs()
        assert len(msgs) == 1 and "BACKGROUND JOB j1 FINISHED" in msgs[0]
        assert jr.collect_finished_jobs() == []  # exactly once

    def test_drain_preview_points_at_full_output(self, monkeypatch, tmp_path):
        jr = self._mkjob(monkeypatch, tmp_path, output="FINDING: admin panel\n" + "y" * 900)
        m = jr.collect_finished_jobs()[0]
        assert "FINDING: admin panel" in m and "job_output j1" in m

    def test_failed_job_announces_failure(self, monkeypatch, tmp_path):
        jr = self._mkjob(monkeypatch, tmp_path, status="failed", output="")
        jr._jobs["j1"]["error"] = "exit 127 nmap not found"
        m = jr.collect_finished_jobs()[0]
        assert "FAILED" in m and "exit 127" in m

    def test_announced_jobs_stay_silent(self, monkeypatch, tmp_path):
        jr = self._mkjob(monkeypatch, tmp_path)
        jr.mark_announced("j1")
        assert jr.collect_finished_jobs() == []

    def test_running_jobs_do_not_drain(self, monkeypatch, tmp_path):
        jr = self._mkjob(monkeypatch, tmp_path, status="running")
        assert jr.collect_finished_jobs() == []

    def test_status_shows_full_output_when_done(self, monkeypatch, tmp_path):
        jr = self._mkjob(monkeypatch, tmp_path, output="K" * 1200)
        s = jr.status("j1")
        assert "K" * 1100 in s  # untruncated (old clip was 500)

    def test_status_clips_running_partial(self, monkeypatch, tmp_path):
        jr = self._mkjob(monkeypatch, tmp_path, status="running", output="R" * 1200)
        s = jr.status("j1")
        assert "partial" in s and "R" * 600 not in s

    def test_job_wait_not_auto_backgrounded(self):
        """A wait on a still-running job must return its verdict inline —
        never become a background job itself (the old absurdity)."""
        import time as _t

        from suijin.modules.agent.lib.nodes.execute_tool_node import execute_tool_node
        from suijin.modules.tools.lib import job_registry as jr

        jid = jr.spawn("sleeper", {"cmd": "sleep 8"}, lambda n, a, c: _t.sleep(8) or "done")
        try:
            out = asyncio.run(
                execute_tool_node(
                    {
                        "_current_step": {
                            "tool_name": "job_wait",
                            "tool_args": {"job_id": jid, "timeout": 1},
                            "iteration": 2,
                        },
                        "current_phase": "informational",
                    },
                    route_tool_fn=lambda n, a, c: jr.wait(a.get("job_id", ""), timeout=1),
                )
            )
            res = out["_current_step"]["tool_output"]
            assert "AUTO-BG" not in res and "still running" in res
        finally:
            jr.mark_announced(jid)
            jr.cancel(jid)
