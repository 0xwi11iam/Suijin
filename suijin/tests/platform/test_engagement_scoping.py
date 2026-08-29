"""Per-engagement state scoping — the immortal-root-state fix.

Schema/recovery/scratchpad/approvals live under outputs/engagements/<slug>/,
die with the engagement (archive on end), and recovery REFUSES garbage
objectives (pasted policy pages masquerading as objectives)."""

import json

import pytest

import suijin.modules.platform.lib.workspace as ws


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    ws._CURRENT_ENGAGEMENT = None
    yield tmp_path
    ws._CURRENT_ENGAGEMENT = None


class TestEngagementDir:
    def test_set_then_paths_scope(self):
        d = ws.set_engagement("Test http://target.example")
        assert d.is_dir() and d.parent.name == "engagements"
        assert "target_example" in d.name or "Test" in d.name
        assert ws.engagement_dir() == d

    def test_fresh_state_per_engagement(self):
        d1 = ws.set_engagement("first objective")
        (d1 / "recovery.json").write_text("{}")
        d2 = ws.set_engagement("second objective")
        assert d2 != d1 and not (d2 / "recovery.json").exists()

    def test_archive_moves_and_empties(self):
        d = ws.set_engagement("to archive")
        (d / "schema.json").write_text("{}")
        dest = ws.archive_engagement("ended")
        assert dest is not None and dest.is_dir() and (dest / "schema.json").is_file()
        assert not d.exists()
        assert ws.archive_engagement("ended") is None  # nothing current


class TestRecoveryGate:
    def test_garbage_objective_refused(self, tmp_path):
        from suijin.modules.agent.lib import engagement as eng

        ws.set_engagement("gate test")
        eng._recovery_path().write_text(
            json.dumps({"objective": "Helvetica;;\n;;\n\\*;;\n\n# Program Rules\n* stuff", "phase": "x"})
        )
        assert eng.load_session_state() is None  # refused, not restored
        assert not eng._recovery_path().exists()  # quarantined away

    def test_real_objective_restores(self):
        from suijin.modules.agent.lib import engagement as eng

        ws.set_engagement("gate test real")
        eng._recovery_path().write_text(
            json.dumps({"objective": "Find and exploit vulnerabilities on http://t.example", "phase": "recon"})
        )
        data = eng.load_session_state()
        assert data is not None and data["phase"] == "recon"

    def test_schema_paths_are_scoped(self):
        from suijin.modules.agent.lib import engagement as eng

        d = ws.set_engagement("schema scoping")
        assert eng._schema_path().parent == d
        assert eng._schema_path().name == "schema.json"
        assert eng._recovery_path().name == "recovery.json"


class TestScratchpadScoping:
    def test_scratchpad_is_per_engagement(self):
        from suijin.modules.agent.lib import scratchpad as sp

        d = ws.set_engagement("pad test")
        assert sp.scratchpad_path().parent == d

    def test_operator_tag_becomes_guidance_memory(self):
        from suijin.modules.agent.lib import scratchpad as sp

        ws.set_engagement("tag test")
        sp.append_note("found admin panel", category="operator")
        body = sp.read_scratchpad()
        assert "[guidance-memory]" in body and "[operator]" not in body
