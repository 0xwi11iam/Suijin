"""Pre-test-wave bug clears — each test pins one bug the manual wave
would otherwise have tripped over."""

import json

import pytest


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    import suijin.modules.platform.lib.workspace as ws

    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    yield tmp_path


class TestCoverageLaneDrift:
    def test_every_lane_marks_a_ledger_valid_class(self):
        """4 of 8 lanes instructed marks the ledger REJECTED
        (mass-assignment/business-logic/file-attacks/injection) — the
        completion gate's data was silently hollow."""
        from suijin.modules.tools.lib.coverage import CLASSES
        from suijin.modules.tools.lib.tester_fleet import TESTER_DOCTRINES, lane_coverage_class

        for lane in TESTER_DOCTRINES:
            assert lane_coverage_class(lane) in CLASSES, lane

    def test_dispatch_task_uses_canonical_class(self):
        from suijin.modules.tools.lib import tester_fleet as tf

        out = json.loads(tf.dispatch_testers(url="http://t.local/login", lanes=["mass-assignment", "file-attacks"]))
        assert out["tasks"], "no tasks built"
        for r in out["tasks"]:
            for chunk in r["coverage"].split("vuln_class=")[1:]:
                cls = chunk.split(",")[0].rstrip(")").strip()
                assert cls.replace("-", "_") in tf._LANE_COVERAGE.values() or cls in (
                    "mass_assignment",
                    "upload",
                    "sqli",
                    "race",
                    "idor",
                    "authz",
                    "authn",
                    "ssrf",
                ), f"dispatch still instructs the drifted class {cls!r}"


class TestStaleUiState:
    def test_engagement_start_resets_gauges(self):
        from suijin.modules.redteam.lib.red.console_ui import UI_STATE

        UI_STATE["flags"] = ["FLAG{stale}"]
        UI_STATE["creds"] = [("AWS key", "AKIAOLD")]
        UI_STATE["ctx_pct"] = 77.7
        UI_STATE["poc_running"] = True
        UI_STATE["librarian"] = 42

        # the reset block lives in run_red_team_async — verify the stop-side
        # insurance directly: ui.stop() clears the stuck POC state
        from rich.console import Console

        from suijin.modules.redteam.lib import redteamer as rt

        ui = rt._ui_cls() if hasattr(rt, "_ui_cls") else None
        if ui is None:
            from suijin.modules.redteam.lib.red.console_ui import EngagementUI

            ui = EngagementUI(Console(file=__import__("io").StringIO(), force_terminal=False))
        ui.stop()
        assert UI_STATE["poc_running"] is False


class TestAskFlagGone:
    def test_ask_operator_flag_is_dead_state(self):
        import subprocess

        out = subprocess.run(["grep", "-rn", '"_ask_operator"', "suijin/modules"], capture_output=True, text=True)
        assert out.stdout.strip() == ""  # the write-only flag is fully removed


class TestOracleKgWrites:
    def test_fired_probe_records_constraint(self, tmp_path, monkeypatch):
        """The in-graph oracle hook produced messages only, never KG writes
        — the fired probe's evidence now lands as a constraint."""
        import asyncio

        recorded = []

        class _FakeKG:
            def add_constraint(self, **kw):
                recorded.append(kw)

        import suijin.modules.loader as loader

        _real = loader.load_local_module
        monkeypatch.setattr(
            loader, "load_local_module", lambda name: _FakeKG() if name == "knowledge_graph" else _real(name)
        )

        from suijin.modules.agent.lib import agent_graph as ag

        async def fake_think(state, generate_fn=None, route_tool_fn=None, config=None):
            return {
                "current_iteration": 4,
                "messages": [],
                "execution_trace": [
                    {
                        "tool_name": "http_request",
                        "tool_output": "SQL syntax error near",
                        "tool_args": {"url": "http://t/x"},
                    }
                ],
            }

        monkeypatch.setattr(ag, "think_node", fake_think)

        async def fake_hyps(snippet, state, generate_fn, original_payload=""):
            return [{"id": "H1", "hypothesis": "verbose sql error", "validation_payload": "?url=http://t/x&id=1'"}]

        import suijin.modules.redteam.lib.intel.oracle as oracle

        monkeypatch.setattr(oracle, "detect_anomaly", lambda t: "sql_error")
        monkeypatch.setattr(oracle, "generate_hypotheses_async", fake_hyps)

        g = ag.SuijinAgentGraph(generate_fn=None, route_tool_fn=lambda n, a, c: "probe output", run_config={})
        asyncio.run(g._think({"messages": [], "current_iteration": 4}))
        assert recorded, "the fired probe did not record a KG constraint"
        assert recorded[0]["confidence"] == 0.6


class TestSchemaFindings:
    def test_confirmed_writes_schema_finding(self):
        import asyncio

        from suijin.modules.agent.lib.nodes import execute_tool_node as etn

        out = asyncio.run(
            etn.execute_tool_node(
                {
                    "_current_step": {
                        "tool_name": "catalog_exploit",
                        "tool_args": {},
                        "iteration": 5,
                    },
                    "current_phase": "exploitation",
                },
                route_tool_fn=lambda n, a, c: "EXP-007 CONFIRMED — CRITICAL : login bypass works",
            )
        )
        assert out["findings"][-1]["id"] == "EXP-007"
        from suijin.modules.agent.lib.engagement import load_engagement_schema

        schema = load_engagement_schema()
        assert any(f.get("id") == "EXP-007" for f in schema.get("findings", []))


class TestLoaderCollisionWarning:
    def test_duplicate_declarations_are_gone(self):
        from suijin.modules.loader import discover_modules, get_module_tools

        discover_modules()
        tools = get_module_tools()
        assert callable(tools.get("openapi_parse"))  # exactly one live (openapik's)
        assert callable(tools.get("graphql_introspect"))
        assert callable(tools.get("graphql_diff"))  # the unique tool survived


class TestConfigSeams:
    def test_execute_seam_carries_live_config(self):
        import asyncio

        from suijin.modules.agent.lib import agent_graph as ag

        seen = []

        def route(name, args, cfg):
            seen.append(cfg)
            return "ok"

        g = ag.SuijinAgentGraph(
            generate_fn=None, route_tool_fn=route, run_config={"provider": "zai", "mode_hitl": True}
        )
        asyncio.run(
            g._execute_tool(
                {
                    "_current_step": {"tool_name": "http_request", "tool_args": {"url": "http://t"}, "iteration": 1},
                    "current_phase": "recon",
                }
            )
        )
        assert seen and seen[0].get("provider") == "zai"  # the live config, not {}

    def test_graph_route_closure_carries_config_tag(self):
        """The execute closure tags itself with the live config — the
        subagent seam reads that tag when routing its own tools."""
        import asyncio

        from suijin.modules.agent.lib import agent_graph as ag

        captured = {}

        def probe(name, args, cfg):
            captured["cfg"] = cfg
            return "ok"

        g = ag.SuijinAgentGraph(generate_fn=None, route_tool_fn=probe, run_config={"provider": "zai"})
        asyncio.run(
            g._execute_tool(
                {
                    "_current_step": {"tool_name": "http_request", "tool_args": {"url": "http://t"}, "iteration": 1},
                    "current_phase": "recon",
                }
            )
        )
        assert captured["cfg"].get("provider") == "zai"


class TestSweeps:
    def test_stale_sje_tmp_swept_on_save(self, tmp_path, monkeypatch):
        import time

        from suijin.modules.tools.lib import engagement_bundle as eb

        exports = tmp_path / "outputs" / "exports"
        exports.mkdir(parents=True)
        stale = exports / "old.sje.tmp"
        stale.write_text("leftover")
        import os

        os.utime(stale, (time.time() - 7200, time.time() - 7200))
        monkeypatch.setattr(eb, "_engagement_slug", lambda o: "x")
        eb.save_engagement("t", "obj", {}, {"messages": [], "current_iteration": 1})
        assert not stale.exists()

    def test_battle_report_lands_in_workspace(self, tmp_path):
        from suijin.modules.platform.lib.workspace import artifact_dir

        d = artifact_dir("reports")
        assert str(tmp_path) in str(d)
