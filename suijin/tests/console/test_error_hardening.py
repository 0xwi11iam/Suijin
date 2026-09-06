"""Error-hardening regressions — no random crashes, ever.

A verb that raises is ONE red line + exit 1 (never a traceback wall); a
crashing graph boot degrades instead of dying; MCP garbage lines bounce;
the think-crash recovery step no longer routes a phantom tool.
"""

import json

import pytest


class TestCliVerbGuard:
    def test_classifier_formats_one_clean_line(self, capsys):
        from suijin.modules.platform.lib.error_handler import classify_and_handle

        info = classify_and_handle(RuntimeError("kaboom inside a verb"), context="command 'probe'")
        assert info["error_type"] == "RuntimeError" and "kaboom" in info["message"]
        assert info["classification"]  # every error lands in a class with guidance

    def test_real_cli_path_guarded(self, monkeypatch, tmp_path, capsys):
        """The REAL main() dispatch: a verb whose guts raise (corrupt
        config.json) exits 1 with a clean error, not a traceback."""
        import suijin.modules.console.lib.cli as cli

        # status reads config.json — corrupt it to force a JSONDecodeError
        # through the real verb path
        monkeypatch.setattr(cli, "_PKG_DIR", str(tmp_path))
        (tmp_path / "config.json").write_text("{corrupt")
        with pytest.raises(SystemExit) as ei:
            cli.main(["status"])
        assert ei.value.code == 1
        err = capsys.readouterr().err
        assert "error:" in err.lower()
        assert "Traceback" not in err


class TestGraphBootDegrades:
    def test_initialize_crash_degrades_not_dies(self):
        import asyncio

        from suijin.modules.agent.lib.agent_graph import SuijinAgentGraph

        g = SuijinAgentGraph(
            generate_fn=None,
            route_tool_fn=None,
        )
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "suijin.modules.agent.lib.agent_graph.initialize_node",
                lambda *a, **k: (_ for _ in ()).throw(RuntimeError("init exploded")),
            )
            out = asyncio.run(g._initialize({"_objective": "x"}))
        assert "initialize node crashed" in json.dumps(out)
        assert out.get("_consecutive_failures") == 0  # boot degrades, breaker untouched

    def test_think_crash_recovery_step_is_router_falsy(self):
        """Invariant #10: the crash-recovery _current_step must be FALSY —
        the old tool_name='none' dispatched a phantom TOOL NOT FOUND turn."""
        import asyncio

        from suijin.modules.agent.lib.agent_graph import SuijinAgentGraph

        g = SuijinAgentGraph(generate_fn=None, route_tool_fn=None)

        async def boom(state, generate_fn, route_tool_fn=None):
            raise RuntimeError("think exploded")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("suijin.modules.agent.lib.agent_graph.think_node", boom)
            out = asyncio.run(g._think({"current_iteration": 1, "messages": []}))
        assert not out["_current_step"].get("tool_name")
        assert out.get("completion_reason") is None  # 1 failure ≠ run death
        assert out["_consecutive_failures"] == 1


class TestMcpGarbageLines:
    def test_non_dict_line_bounces_not_dies(self, monkeypatch):
        import io

        from suijin.modules.console.lib import mcp_server

        lines = io.StringIO('[1, 2, 3]\n"just a string"\n42\n')
        out_sink = io.StringIO()
        monkeypatch.setattr("sys.stdin", lines)
        monkeypatch.setattr("sys.stdout", out_sink)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(mcp_server, "handle_message", lambda m: {"echo": True})
            mcp_server.main()
        replies = [json.loads(ln) for ln in out_sink.getvalue().splitlines() if ln.strip()]
        assert len(replies) == 3 and all("error" in r for r in replies)  # bounced, loop alive
