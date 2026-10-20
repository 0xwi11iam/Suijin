"""Authorization + mesh scoping + ending-path fixes (2026-10-20 round 3).

Field origin: a real engagement declined at iter 87 because (a) the
authorization contract was not inspectable, (b) the operator's
re-affirmation carried no terminal weight, and (c) DEAD peers' exploit
broadcasts from a *different* engagement were visible in the shared
mesh group chat — the model reasoned the operation covered two
unrelated targets and refused. These tests pin the fixes.
"""

import json
from pathlib import Path

import pytest

from suijin.modules.agent.lib.authorization import (
    authorization_block,
    detect_affirmation,
    ensure_authorization,
    hosts_from_objective,
    is_decline_reason,
)


class TestAuthorizationArtifact:
    def test_hosts_from_objective(self):
        assert hosts_from_objective("test https://a.example/x and b.example y") == ["a.example", "b.example"]

    def test_ensure_writes_contract(self, tmp_path, monkeypatch):
        import suijin.modules.platform.lib.workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        paths = ensure_authorization("Hunt on target.example for exposed maps")
        assert paths and "target.example.md" in paths[0]
        body = Path(paths[0]).read_text()
        assert "Authorized by" in body and "target.example" in body

    def test_block_cites_contract_and_closed_ruling(self):
        st = {"original_objective": "probe https://t.example deeply"}
        b = authorization_block(st)
        assert "config/authorizations/t.example.md" in b and "SETTLED BY THE OPERATOR" in b
        st["_auth_final"] = True
        assert "CLOSED" in authorization_block(st)


class TestAffirmationDetection:
    def test_operator_affirmations_match(self):
        for t in (
            "You are authorized to test this target, continue.",
            "I authorize this engagement — proceed.",
            "Authorization is confirmed by me, the operator. Resume.",
            "yes, authorized. keep going",
        ):
            assert detect_affirmation(t), t

    def test_non_affirmations_dont_match(self):
        for t in ("continue", "try the sourcemap lane next", "what happened to the bundle job?", ""):
            assert not detect_affirmation(t), t


class TestDeclineReason:
    def test_decline_patterns(self):
        for r in (
            "Engagement declined. The target is third-party infrastructure",
            "Declining this engagement as unauthorized",
            "I will not test this target",
            "outside the scope of my authorization",
        ):
            assert is_decline_reason(r), r

    def test_results_reasons_dont_match(self):
        for r in ("Objective complete: 3 findings cataloged", "budget_exhausted", ""):
            assert not is_decline_reason(r), r


class TestMeshEngagementScoping:
    """The incident: dead peers' lines from ANOTHER engagement rendered
    as if this operation covered two targets."""

    @pytest.fixture(autouse=True)
    def _mesh(self, tmp_path, monkeypatch):
        import suijin.modules.agent.lib.mesh as mesh

        monkeypatch.setattr(mesh, "MESH_DIR_OVERRIDE", tmp_path)
        monkeypatch.setattr(mesh, "_node", {"me": None, "threads": []})
        mesh._tail_reset()
        self.mesh = mesh
        self.dir = tmp_path
        yield
        mesh._tail_reset()

    def _node_with_engagement(self, slug):
        import suijin.modules.platform.lib.workspace as ws

        orig = ws._CURRENT_ENGAGEMENT
        ws._CURRENT_ENGAGEMENT = self.dir / f"20261020_000000_{slug}" if slug else None
        self.mesh._node["me"] = None
        self.mesh.start(summary=f"objective {slug}", phase="testing")
        me = self.mesh._node["me"]
        if orig is not None:
            ws._CURRENT_ENGAGEMENT = orig
        else:
            ws._CURRENT_ENGAGEMENT = None
        return me

    def test_node_carries_engagement_slug(self):
        me = self._node_with_engagement("alpha-lane")
        assert me.get("engagement") == "20261020_000000_alpha-lane"

    def test_lines_tagged_and_scoped(self):
        self._node_with_engagement("alpha-lane")
        self.mesh.broadcast("same-engagement message")
        # a DIFFERENT engagement's line (as a dead peer would write)
        gc = self.dir / "gc.log"
        with gc.open("a", encoding="utf-8") as f:
            f.write(
                "11:00:00|99999|agent||legacy untagged line\n"
                "11:00:01|99999|agent|20261020_000000_beta-lane|other operation chatter\n"
            )
        self.mesh._tail_reset()
        lines = self.mesh.poll_chat()
        joined = " ".join(lines)
        assert "same-engagement message" in joined
        assert "other operation chatter" not in joined  # different engagement
        assert "legacy untagged line" not in joined  # stale residue hidden

    def test_untagged_node_sees_everything(self):
        self._node_with_engagement(None)  # no engagement set
        gc = self.dir / "gc.log"
        gc.write_text("11:00:00|99999|agent||legacy line\n", encoding="utf-8")
        self.mesh._tail_reset()
        assert any("legacy line" in ln for ln in self.mesh.poll_chat())

    def test_system_announcements_tagged(self):
        self._node_with_engagement("alpha-lane")
        self.mesh._announce_system("node-1 phase → exploitation")
        line = (self.dir / "gc.log").read_text().strip().splitlines()[-1]
        parts = line.split("|")
        assert len(parts) >= 5 and parts[3] == "20261020_000000_alpha-lane"

    def test_publish_state_carries_engagement(self):
        self._node_with_engagement("alpha-lane")
        self.mesh.publish_state({"phase": "exploitation"})
        d = json.loads((self.dir / f"{self.mesh._node['me']['pid']}-state.json").read_text())
        assert d.get("engagement") == "20261020_000000_alpha-lane"


class TestBbpPathFix:
    def test_report_lands_inside_engagement(self, tmp_path):
        from suijin.modules.tools.lib.bug_bounty_report import generate_bug_bounty_report

        eng = tmp_path / "20261020_000000_demo"
        eng.mkdir()
        path = generate_bug_bounty_report(
            "demo engagement",
            [
                {
                    "title": "demo finding",
                    "severity": "high",
                    "type": "idor",
                    "endpoint": "https://t.example/api/x",
                    "description": "object access",
                    "evidence": "200 with other tenant data",
                    "poc": ["curl https://t.example/api/x?id=2"],
                }
            ],
            engagement_dir=eng,
        )
        assert path.parent.parent == eng  # inside <eng>/reports/
        assert path.exists()


class TestCoverageDefaultGuard:
    def test_no_default_litter(self, tmp_path, monkeypatch):
        from suijin.modules.tools.lib import coverage as cov

        stray = tmp_path / "engagements" / "_default"
        monkeypatch.setattr(cov, "_store_path", lambda: stray / "coverage.json")
        cov.mark("https://t.example", "authn", "not_applicable", evidence="x" * 60)
        assert not stray.exists()  # the guard dropped the write entirely


class TestDiagPath:
    def test_diag_writes_to_home_logs_single_segment(self, tmp_path, monkeypatch):
        import suijin.kernel.diag as diag

        monkeypatch.setenv("SUIJIN_WORKSPACE", str(tmp_path))
        p = diag._log_path()
        assert p == tmp_path / "logs" / "diag.log"
        assert "logs/logs" not in str(p)
