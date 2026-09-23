"""Per-engagement state scoping — the immortal-root-state fix.

Schema/scratchpad/approvals live under outputs/engagements/<slug>/ and
die with the engagement (the .sje bundle is the resume artifact).
objectives (pasted policy pages masquerading as objectives)."""


import pytest

import suijin.modules.platform.lib.workspace as ws


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    ws._reset_engagement()  # hermetic: no engagement pinned from another test
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
        d2 = ws.set_engagement("second objective")
        assert d2 != d1

    def test_archive_moves_and_empties(self):
        d = ws.set_engagement("to archive")
        (d / "schema.json").write_text("{}")
        dest = ws.archive_engagement("ended")
        assert dest is not None and dest.is_dir() and (dest / "schema.json").is_file()
        assert not d.exists()
        assert ws.archive_engagement("ended") is None  # nothing current


class TestScratchpadScoping:
    def test_scratchpad_is_per_engagement(self):
        from suijin.modules.agent.lib import scratchpad as sp

        d = ws.set_engagement("pad test")
        assert sp.scratchpad_path().parent == d / "state"  # 2026-09-16: state lives in state/

    def test_operator_tag_becomes_guidance_memory(self):
        from suijin.modules.agent.lib import scratchpad as sp

        ws.set_engagement("tag test")
        sp.append_note("found admin panel", category="operator")
        body = sp.read_scratchpad()
        assert "[guidance-memory]" in body and "[operator]" not in body
