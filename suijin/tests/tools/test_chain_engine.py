"""The chain engine — indicator firing, curated templates, the loom.

The operator's diagnosis: the model knows syntax but can't HOLD a chain
across tool results. These pin the three mechanisms.
"""

import json
from pathlib import Path

import pytest

from suijin.server.tools.lib import chain_engine as ce


@pytest.fixture(autouse=True)
def _seed_only(monkeypatch, tmp_path):
    """Tests run against the in-tree seed (never an operator workspace copy)."""
    monkeypatch.setattr("suijin.modules.platform.lib.workspace.WORKSPACE_DIR", tmp_path, raising=False)
    # force the seed path even if a workspace config exists
    monkeypatch.setattr(Path, "is_file", lambda self: False, raising=False) if False else None
    return None


class TestTemplates:
    def test_seed_loads_and_is_wellformed(self):
        tpl = ce.load_templates()
        assert len(tpl) >= 12
        for t in tpl:
            assert t.get("id") and t.get("name")
            assert t.get("indicators"), f"{t['id']} has no indicators"
            assert t.get("steps"), f"{t['id']} has no steps"
            assert t.get("exit"), f"{t['id']} has no exit criteria"
            for step in t["steps"]:
                assert step.get("tool"), f"{t['id']} step missing tool"

    def test_every_indicator_regex_compiles(self):
        import re

        for t in ce.load_templates():
            for ind in t["indicators"]:
                re.compile(ind)  # raises on a malformed pattern

    def test_operators_archetypes_covered(self):
        ids = set(ce.template_ids())
        for expected in (
            "api_openapi",
            "xss_token_steal",
            "jwt_confusion",
            "idor_object_sweep",
            "ssrf_internal",
            "file_upload_rce",
            "rce_to_internal",
            "privesc_linux",
            "privesc_container",
            "bypass_403_chain",
        ):
            assert expected in ids, expected

    def test_workspace_templates_override_the_seed(self, tmp_path, monkeypatch):
        custom = [{"id": "custom_chain", "name": "operator tune", "indicators": ["zzz"], "steps": [{"tool": "plan"}]}]
        (tmp_path / "config").mkdir(exist_ok=True)
        (tmp_path / "config" / "chain_templates.json").write_text(json.dumps(custom))
        import suijin.modules.platform.lib.workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        assert ce.template_ids() == ["custom_chain"]


class TestIndicatorFiring:
    def test_openapi_indicator_fires(self):
        text, fired = ce.scan_indicators('Status: 200\n{"openapi": "3.0.0", "paths": {"/v1/users": {}}}')
        assert "api_openapi" in fired
        assert "chain_suggest('api_openapi')" in text

    def test_iframe_fires_xss_chain(self):
        text, fired = ce.scan_indicators("<html><body><iframe src='/app'></iframe></body></html>")
        assert "xss_token_steal" in fired

    def test_jwt_fires(self):
        text, fired = ce.scan_indicators(
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123DEF456_xyz"
        )
        assert "jwt_confusion" in fired

    def test_already_fired_not_refired(self):
        _, fired1 = ce.scan_indicators('{"openapi": "3.0"}')
        text2, fired2 = ce.scan_indicators('{"openapi": "3.0"}', already_fired=fired1)
        assert fired2 == [] and text2 == ""

    def test_quiet_output_fires_nothing(self):
        text, fired = ce.scan_indicators("Status: 200\nServer: nginx\n\nOK")
        assert fired == [] and text == ""


class TestSuggest:
    def test_suggest_renders_executable_draft(self):
        out = ce.suggest("api_openapi")
        assert "CHAIN TEMPLATE" in out
        assert "preconditions" in out and "steps:" in out
        assert "http_request" in out
        assert "exit criteria" in out

    def test_unknown_template_lists_known(self):
        out = ce.suggest("nope")
        assert "No chain template" in out and "api_openapi" in out


class TestLoom:
    def test_track_and_render(self):
        plan = ce.track(None, "api_openapi", "start")
        plan = ce.track(plan, "api_openapi", "confirm", node="api_schema_readable", note="43 routes")
        block = ce.loom_render(plan)
        assert "api_openapi" in block and "api_schema_readable:✔" in block
        assert "43 routes" in block

    def test_confirm_unlocks_dependents(self):
        plan = ce.track(None, "xss_token_steal", "confirm", node="stolen_token")
        block = ce.loom_render(plan)
        assert "session_replay" in plan  # unlocked by the stolen_token primitive
        assert "session_replay" in block and "○" in block  # available icon

    def test_dead_marks_the_template(self):
        plan = ce.track(None, "git_exposed", "dead")
        assert plan["git_exposed"]["status"] == "dead"
        assert "✗" in ce.loom_render(plan)

    def test_empty_plan_renders_nothing(self):
        assert ce.loom_render(None) == ""
        assert ce.loom_render({}) == ""


class TestCatalog:
    def test_chain_tools_visible(self):
        from suijin.modules.agent.lib.prompts.tool_registry import _ALL_TOOLS, TOOL_REGISTRY

        for t in ("chain_suggest", "chain_track"):
            assert t in _ALL_TOOLS and t in TOOL_REGISTRY


class TestDispatch:
    def test_chain_suggest_routes(self):
        from suijin.server.tools.lib import dispatch

        out = dispatch.route_tool("chain_suggest", {"template": "api_openapi"}, config={})
        assert "CHAIN TEMPLATE" in out
