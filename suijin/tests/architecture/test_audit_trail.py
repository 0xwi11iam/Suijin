"""Audit trail v2 — every surface leaves an append-only record.

Contract: a tool invoked through ANY surface (kernel ctx.call_tool, the
agent execute loop, a CLI verb) produces a JSONL entry under
<workspace>/outputs/audit_trails/ — with arg KEY NAMES and a digest but
NEVER raw values (secrets must not land in the audit log), and entries
are only ever appended, never rewritten.
"""

import asyncio
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MODULES = REPO / "suijin" / "modules"


class TestKernelSurface:
    def test_call_tool_audited(self, tmp_path):
        from suijin.kernel import controller

        ctx, _ = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        ctx.call_tool("search_kb", {"keyword": "sqli"})
        ctx.tool_audit.flush()
        entries = ctx.tool_audit.entries()
        hit = [e for e in entries if e["name"] == "search_kb"]
        assert hit, f"no audit entry for search_kb in {len(entries)} entries"
        e = hit[-1]
        assert e["surface"] == "kernel"
        assert e["outcome"] in ("ok", "tool-error")  # KB may be absent -> tool-error is fine
        ctx.shutdown()

    def test_args_are_digested_never_raw(self, tmp_path):
        from suijin.kernel import controller

        ctx, _ = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        ctx.call_tool("search_kb", {"keyword": "sqli"})
        ctx.tool_audit.flush()
        raw = (tmp_path / "outputs" / "audit_trails" / "tool_calls.jsonl").read_text()
        assert "sqli" not in raw  # the VALUE never appears
        entry = json.loads(raw.strip().splitlines()[-1])
        assert entry["args"]["keys"] == ["keyword"]
        assert len(entry["args"]["sha256"]) == 16
        ctx.shutdown()

    def test_unknown_tool_audited(self, tmp_path):
        from suijin.kernel import controller

        ctx, _ = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        ctx.call_tool("no_such_tool", {})
        ctx.tool_audit.flush()
        assert any(e["name"] == "no_such_tool" and e["outcome"] == "unknown-tool" for e in ctx.tool_audit.entries())
        ctx.shutdown()

    def test_append_only(self, tmp_path):
        from suijin.kernel import controller

        ctx, _ = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        ctx.call_tool("search_kb", {"keyword": "a"})
        ctx.tool_audit.flush()
        first = (tmp_path / "outputs" / "audit_trails" / "tool_calls.jsonl").read_text()
        ctx.call_tool("search_kb", {"keyword": "b"})
        ctx.tool_audit.flush()
        second = (tmp_path / "outputs" / "audit_trails" / "tool_calls.jsonl").read_text()
        assert second.startswith(first) and len(second) > len(first)
        ctx.shutdown()


class TestCliSurface:
    def test_verb_audited(self, tmp_path, monkeypatch):
        import suijin.modules.console.lib.cli as cli
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        audit_dir = tmp_path / "outputs" / "audit_trails"
        import contextlib
        import io

        with pytest.raises(SystemExit) as ei, contextlib.redirect_stdout(io.StringIO()):
            cli.main(["version"])
        assert ei.value.code in (0, None)
        path = audit_dir / "cli_calls.jsonl"
        assert path.exists(), "CLI invocation left no audit entry"
        entry = json.loads(path.read_text().strip().splitlines()[-1])
        assert entry["surface"] == "cli" and entry["name"] == "version"


class TestAgentSurface:
    def test_execute_step_audited(self, tmp_path, monkeypatch):
        from suijin.modules.agent.lib.nodes.execute_tool_node import execute_tool_node
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)

        async def fake_route(tool_name, args, config):
            return "ok result"

        state = {"_current_step": {"tool_name": "search_kb", "tool_args": {"keyword": "x"}}, "current_iteration": 3}
        out = asyncio.run(execute_tool_node(state, route_tool_fn=fake_route))
        assert out["_tool_result"]["success"]
        path = tmp_path / "outputs" / "audit_trails" / "agent_steps.jsonl"
        assert path.exists(), "agent step left no audit entry"
        entry = json.loads(path.read_text().strip().splitlines()[-1])
        assert entry["surface"] == "agent"
        assert entry["name"] == "search_kb"
        assert entry["outcome"] == "ok"
        assert "iteration=3" in entry.get("detail", "")
        raw = path.read_text()
        assert '"x"' not in raw  # arg VALUE never stored


class TestDigest:
    def test_digest_shape(self):
        from suijin.kernel.audit import digest_args

        d = digest_args({"b": 1, "a": "secret"})
        assert d["keys"] == ["a", "b"]
        assert "secret" not in json.dumps(d)
        assert d["n_bytes"] > 0 and len(d["sha256"]) == 16
