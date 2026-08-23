"""H5 — claim-time verification + memory repair."""

import json

import pytest


@pytest.fixture(autouse=True)
def _isolated_ws(tmp_path, monkeypatch):
    from suijin.modules.platform.lib import workspace as ws

    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    yield tmp_path


class TestClaimTimeVerification:
    def test_recording_carries_verdict(self, monkeypatch):
        """record_finding now grades the claim immediately — the agent sees
        VERIFIED/DOWNGRADED on the result line next turn."""
        # stub the loader import inside the function via sys.modules
        import sys

        from suijin.modules.tools.lib import intel

        class FakeKGMod:
            @staticmethod
            def add_constraint(*a, **k):
                pass

        real_loader = "suijin.modules.loader"
        import types

        fake_loader = types.ModuleType(real_loader)
        fake_loader.load_local_module = lambda n: FakeKGMod()
        monkeypatch.setitem(sys.modules, real_loader, fake_loader)
        monkeypatch.setattr(intel, "_safe_route", lambda name, args: "reflection: <script>alert(1)</script> captured")
        out = intel.record_finding("10.0.0.9", "behavior", "xss-reflection", evidence="<script>alert(1)</script>")
        assert "Recorded:" in out and "Verification:" in out

    def test_new_recipes_cover_more_classes(self):
        from suijin.modules.agent.lib.verify import _RECIPE_ALIASES, _RECIPES, verify_finding

        assert len(_RECIPES) >= 10
        # aliases map real-world type names to recipes
        for alias in ("sql_injection", "rce", "bola", "ssrf_vulnerability"):
            assert alias in _RECIPE_ALIASES
        out = verify_finding(
            {"type": "rce", "target": "10.0.0.9", "evidence": "uid=0"}, route_fn=lambda n, a, c: "nope"
        )
        assert out["verification"]["verdict"] in ("downgraded", "dismissed", "unverifiable", "verified")

    def test_keyword_fallback_is_dead(self):
        """'output mentions the class' used to count as VERIFIED — it must
        not anymore."""
        from suijin.modules.agent.lib.verify import verify_finding

        # second path mentions 'sql' but shows NO marker and NO error echo
        out = verify_finding(
            {"type": "sqli", "target": "10.0.0.9", "evidence": "weird-long-evidence-string-9137"},
            route_fn=lambda n, a, c: "the scan notes this might be sql-related, unclear",
        )
        assert out["verification"]["verdict"] != "verified"

    def test_marker_still_verifies(self):
        from suijin.modules.agent.lib.verify import verify_finding

        out = verify_finding(
            {"type": "xss", "target": "10.0.0.9", "evidence": "<script>alert(1)</script>"},
            route_fn=lambda n, a, c: "captured reflection: <script>alert(1)</script> in body",
        )
        assert out["verification"]["verdict"] == "verified"


class TestMemoryRepair:
    def test_note_arity_fixed_and_persists(self):
        """The bug: note(text) called where note(target, text) was the
        signature — TypeError swallowed, ZERO memory ever written despite
        361 sessions."""
        from suijin.modules.agent.lib import memory as _mem

        _mem.note("target.example", "operator confirmed scope")
        data = json.loads((_mem._target_file("target.example")).read_text())
        assert any("confirmed scope" in n for n in data["operator_notes"])

    def test_record_engagement_writes(self):
        from suijin.modules.agent.lib import memory as _mem

        _mem.record_engagement("target.example", "test objective", {"iterations": 3})
        data = json.loads((_mem._target_file("target.example")).read_text())
        assert data["engagements"] and data["engagements"][0]["objective"] == "test objective"

    def test_recall_returns_target_history(self):
        from suijin.modules.agent.lib import memory as _mem

        _mem.record_engagement("target.example", "obj one", {"iterations": 1})
        _mem.note("target.example", "found admin panel")
        out = _mem.recall("target.example")
        assert "target.example" in out and "obj one" in out


class TestScratchpadDedupe:
    def test_duplicate_burst_suppressed(self):
        """The poisoning: an auto-logged line repeated 30x became the next
        run's top lead. Now: one entry per burst."""
        from suijin.modules.agent.lib import scratchpad as sp

        sp.scratchpad_path().unlink(missing_ok=True)
        for _ in range(6):
            sp.append_note("found admin panel", category="operator")
        lines = sp.scratchpad_path().read_text().splitlines()
        assert len(lines) == 1

    def test_different_notes_all_land(self):
        from suijin.modules.agent.lib import scratchpad as sp

        sp.scratchpad_path().unlink(missing_ok=True)
        sp.append_note("found admin panel", category="operator")
        sp.append_note("sqli confirmed on /search", category="recon")
        sp.append_note("port 8080 open", category="recon")
        assert len(sp.scratchpad_path().read_text().splitlines()) == 3


class TestEndOfRunMemoryWiring:
    def test_record_engagement_called_at_end(self):
        import inspect

        import suijin.modules.redteam.lib.redteamer as rt

        src = inspect.getsource(rt.run_red_team_async)
        assert "_mem.record_engagement(" in src  # the 361-sessions-no-memory fix


class TestH6Telemetry:
    def test_audit_row_carries_iteration(self, tmp_path, monkeypatch):
        """Every row in every agent_steps.jsonl read 'iteration=?' — the
        audit read a state key that isn't set at execute time."""
        import asyncio

        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        from suijin.modules.agent.lib.nodes.execute_tool_node import execute_tool_node

        asyncio.run(
            execute_tool_node(
                {
                    "_current_step": {"tool_name": "http_request", "tool_args": {"url": "https://t.io/"}, "iteration": 7},
                    "current_phase": "informational",
                },
                route_tool_fn=lambda n, a, c: "Status: 200",
            )
        )
        rows = (tmp_path / "outputs" / "audit_trails" / "agent_steps.jsonl").read_text().splitlines()
        assert rows and "iteration=7" in rows[-1] and "iteration=?" not in rows[-1]
