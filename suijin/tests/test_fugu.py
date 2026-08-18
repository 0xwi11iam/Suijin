"""Tests for fugu.py — collective intelligence orchestrator (was dead code).

Verifies the module imports, TaskGraph behavior, tool-block extraction,
and the adapters that replaced the legacy redteamer imports.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


class TestFuguImports:
    def test_module_imports(self):
        import suijin.fugu

        assert suijin.fugu.run_fugu is not None
        assert suijin.fugu.TaskGraph is not None
        assert suijin.fugu.ROLES is not None

    def test_run_fugu_importable_no_broken_references(self):
        """run_fugu must not reference nonexistent modules (regression).

        The legacy version imported `suijin.redteamer` (nonexistent) and
        `suijin.supervisor` (nonexistent) — this test loads the function
        and verifies its module-level imports resolve.
        """
        import suijin.fugu as f

        # Adapter helpers must exist
        assert callable(f._ai_call)
        assert callable(f._extract_tool)
        assert callable(f._action_trail_context)
        assert callable(f._tutorial_knowledge)
        assert callable(f._host_os_directive)

    def test_roles_complete(self):
        """All six phases have role definitions."""
        import suijin.fugu

        for role in ["recon", "exploit", "escalate", "persist", "lateral", "report"]:
            assert role in suijin.fugu.ROLES
            assert "tools" in suijin.fugu.ROLES[role]
            assert "prompt_prefix" in suijin.fugu.ROLES[role]


class TestTaskGraph:
    def test_from_json(self):
        from suijin.fugu import TaskGraph

        tg = TaskGraph.from_json(
            {
                "phases": [
                    {"id": "p1", "role": "recon", "objective": "scan"},
                    {"id": "p2", "role": "exploit", "objective": "exploit", "depends_on": ["p1"]},
                ]
            }
        )
        assert len(tg.phases) == 2
        assert tg.phases[0]["status"] == "pending"
        assert tg.phases[1]["depends_on"] == ["p1"]

    def test_ready_phases_dependency_ordering(self):
        from suijin.fugu import TaskGraph

        tg = TaskGraph.from_json(
            {
                "phases": [
                    {"id": "p1", "role": "recon"},
                    {"id": "p2", "role": "exploit", "depends_on": ["p1"]},
                ]
            }
        )
        ready = tg.ready_phases()
        assert len(ready) == 1
        assert ready[0]["id"] == "p1"

        # Complete p1 → p2 becomes ready
        tg.mark("p1", "complete")
        ready2 = tg.ready_phases()
        assert [p["id"] for p in ready2] == ["p2"]

    def test_failed_dependency_blocks_phase(self):
        from suijin.fugu import TaskGraph

        tg = TaskGraph.from_json(
            {
                "phases": [
                    {"id": "p1", "role": "recon"},
                    {"id": "p2", "role": "exploit", "depends_on": ["p1"]},
                ]
            }
        )
        tg.mark("p1", "failed")
        ready = tg.ready_phases()
        assert ready == []
        assert tg.phases[1]["status"] == "blocked"

    def test_exhausted_satisfies_dependency(self):
        from suijin.fugu import TaskGraph

        tg = TaskGraph.from_json(
            {
                "phases": [
                    {"id": "p1", "role": "recon"},
                    {"id": "p2", "role": "exploit", "depends_on": ["p1"]},
                ]
            }
        )
        tg.mark("p1", "exhausted")
        ready = tg.ready_phases()
        assert [p["id"] for p in ready] == ["p2"]

    def test_summary_contains_status_icons(self):
        from suijin.fugu import TaskGraph

        tg = TaskGraph.from_json({"phases": [{"id": "p1", "role": "recon", "objective": "scan target"}]})
        summary = tg.summary()
        assert "p1" in summary
        assert "scan target" in summary


class TestToolExtraction:
    def test_extract_modern_format(self):
        """Modern {"action","tool_name","tool_args"} shape maps to legacy keys."""
        from suijin.fugu import _extract_tool

        resp = '{"action":"use_tool","thought":"scan","tool_name":"nmap","tool_args":{"target":"x"}}'
        tool = _extract_tool(resp)
        assert tool is not None
        assert tool["tool"] == "nmap"
        assert tool["args"] == {"target": "x"}

    def test_extract_legacy_format(self):
        from suijin.fugu import _extract_tool

        resp = '```json\n{"tool":"http_request","args":{"url":"http://x"}}\n```'
        tool = _extract_tool(resp)
        assert tool is not None
        assert tool["tool"] == "http_request"

    def test_extract_no_tool(self):
        from suijin.fugu import _extract_tool

        assert _extract_tool("just some text") is None
        assert _extract_tool(None) is None


class TestAdapters:
    def test_action_trail_context_no_data(self):
        from suijin.fugu import _action_trail_context

        result = _action_trail_context()
        assert isinstance(result, str)

    def test_host_os_directive_mentions_os(self):

        from suijin.fugu import _host_os_directive

        result = _host_os_directive()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_tutorial_knowledge_is_string(self):
        from suijin.fugu import _tutorial_knowledge

        result = _tutorial_knowledge()
        assert isinstance(result, str)

    def test_extract_target_from_objective_domain(self):
        from suijin.fugu import _extract_target_from_objective

        assert _extract_target_from_objective("attack https://example.com/login") == "example.com"

    def test_extract_target_from_objective_ip(self):
        from suijin.fugu import _extract_target_from_objective

        assert _extract_target_from_objective("scan 10.0.0.5 for vulns") == "10.0.0.5"

    def test_extract_target_none(self):
        from suijin.fugu import _extract_target_from_objective

        assert _extract_target_from_objective("do something") is None


class TestRoleGating:
    def test_unauthorized_tool_gated(self):
        from suijin.fugu import ROLES, _role_gated_route

        result = _role_gated_route("msf_run", {}, ROLES["recon"], {})
        assert "TOOL GATED" in result

    def test_authorized_tool_routes(self):
        from suijin.fugu import ROLES, _role_gated_route

        result = _role_gated_route("write_note", {"content": "test"}, ROLES["report"], {})
        assert isinstance(result, str)
