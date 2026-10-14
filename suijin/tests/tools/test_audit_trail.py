"""Audit trail — multi-engagement isolation + short-run flushing.

The 2026-10-04 incident: 4 parallel agents produced agent_steps.jsonl
fine but their trail JSONs were EMPTY. Two bugs:
1. module-global _current_trail: concurrent engagements in one process
   shared a single dict, the last start_audit() won
2. time-only throttle: short engagements (< 10s) never crossed the
   flush interval, so iterations marked dirty but never wrote
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from suijin.modules.tools.lib import audit_trail as at  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    at.reset_all()
    at.set_audit_dir(None)
    yield
    at.reset_all()
    at.set_audit_dir(None)


class TestMultiEngagement:
    def test_two_engagements_do_not_clobber(self, tmp_path):
        """The core bug: concurrent trails each land in their own file."""
        dirs = []
        for i in range(3):
            d = tmp_path / f"eng_{i}" / "audit_trails"
            d.mkdir(parents=True)
            dirs.append(d)

        for i, d in enumerate(dirs):
            at.set_audit_dir(d)
            at.start_audit(f"agent_{i} objective")
            at.log_iteration(1, f"t{i}", "r", "http_request", {"url": f"http://x/{i}"}, f"out {i}", True, "recon")
            at.log_finding("sql", "high", "/api", f"finding {i}", "evidence")
            at.end_audit(0.01)

        for i, d in enumerate(dirs):
            files = list(d.glob("*.json"))
            assert len(files) == 1, f"agent {i}: expected 1 json, got {len(files)}"
            with open(files[0]) as fh:
                data = json.load(fh)
            assert len(data["iterations"]) == 1, f"agent {i}: iterations lost"
            assert data["iterations"][0]["thought"] == f"t{i}"  # THEIR thought, not another agent's
            assert len(data["findings"]) == 1
            assert data["findings"][0]["description"] == f"finding {i}"

    def test_short_run_flushes_within_throttle_window(self, tmp_path):
        """5 iterations inside 10s MUST produce a non-empty JSON (the
        count-based force), not wait for the time interval."""
        d = tmp_path / "short" / "audit_trails"
        d.mkdir(parents=True)
        at.set_audit_dir(d, key="short_run")
        at.start_audit("short run")
        for i in range(3):
            at.log_iteration(i, f"t{i}", "r", "http_request", {}, "out", True, "recon")
        # NO end_audit (simulating a crash or still-running agent)
        # the 5th-iteration force hasn't hit yet (3 < 5), but the initial
        # start_audit(force=True) wrote the structure. With 5 iterations
        # the force fires even without end_audit:
        at.log_iteration(3, "t3", "r", "http_request", {}, "out", True, "recon")
        at.log_iteration(4, "t4", "r", "http_request", {}, "out", True, "recon")
        # now check the file has ALL 5 iterations (the %5 force wrote at iteration 5)
        files = list(d.glob("*.json"))
        assert files, "no trail file written"
        with open(files[0]) as fh:
            data = json.load(fh)
        assert len(data["iterations"]) == 5, f"expected 5 iterations, got {len(data['iterations'])}"

    def test_markdown_export_per_engagement(self, tmp_path):
        d1 = tmp_path / "e1" / "audit_trails"
        d2 = tmp_path / "e2" / "audit_trails"
        d1.mkdir(parents=True)
        d2.mkdir(parents=True)

        at.set_audit_dir(d1, key="md_first")
        at.start_audit("first")
        at.log_iteration(1, "t", "r", "tool", {}, "o", True, "recon")
        at.end_audit()

        at.set_audit_dir(d2, key="md_second")
        at.start_audit("second")
        at.log_iteration(1, "t", "r", "tool", {}, "o", True, "recon")
        at.end_audit()

        assert list(d1.glob("*.md")) and list(d2.glob("*.md"))
        md1 = list(d1.glob("*.md"))[0].read_text()
        assert "first" in md1 and "second" not in md1

    def test_end_audit_only_closes_this_engagement(self, tmp_path):
        d1 = tmp_path / "e1" / "audit_trails"
        d2 = tmp_path / "e2" / "audit_trails"
        d1.mkdir(parents=True)
        d2.mkdir(parents=True)

        at.set_audit_dir(d1, key="eng_one")
        at.start_audit("one")
        at.set_audit_dir(d2, key="eng_two")
        at.start_audit("two")

        # end engagement 2; engagement 1 must still be active
        at.set_audit_dir(d2, key="eng_two")
        at.end_audit()
        # engagement 1 can still log
        at.set_audit_dir(d1, key="eng_one")
        at.log_iteration(1, "still alive", "r", "t", {}, "o", True, "recon")
        at.end_audit()

        j1 = list(d1.glob("*.json"))[0]
        with open(j1) as fh:
            data = json.load(fh)
        assert len(data["iterations"]) == 1
        assert data["iterations"][0]["thought"] == "still alive"


class TestLegacyAPI:
    def test_get_audit_json_returns_current(self, tmp_path):
        d = tmp_path / "audit"
        d.mkdir()
        at.set_audit_dir(d, key="legacy")
        at.start_audit("test")
        at.log_iteration(1, "t", "r", "tool", {}, "out", True, "recon")
        out = at.get_audit_json()
        assert out.get("engagement") == "test"
        assert len(out.get("iterations", [])) == 1

    def test_no_trail_is_silent_noop(self, tmp_path):
        at.set_audit_dir(tmp_path)
        # no start_audit — logging must not raise
        at.log_iteration(1, "t", "r", "t", {}, "o", True, "r")
        at.log_finding("x", "y", "z", "w", "v")
        at.flush()
        assert at.end_audit() is None


def test_same_iteration_upserts_no_blank_stub(tmp_path):
    """The 'iteration N, no content' bug: think-side logged a tool=none
    stub for an iteration the execute-side then logged WITH the tool +
    observation — two stacked rows, one blank. The richer entry must
    MERGE into one; a phantom action count must not accrue."""
    at.set_audit_dir(tmp_path)
    at.start_audit("upsert probe")
    at.log_iteration(1, "plan the sweep", "", "", {}, "", True, "recon")  # think-side stub
    at.log_iteration(1, "plan the sweep", "", "mesh_status", {"x": 1}, "roster: 3 nodes", True, "recon")
    live = at.current_trail("upsert probe")
    assert live is not None
    its = live["iterations"]
    assert len(its) == 1, its
    assert its[0]["action"]["tool"] == "mesh_status"
    assert its[0]["observation"] == "roster: 3 nodes"
    assert live["total_actions"] == 1  # the stub did not count as an action


def test_html_and_burp_export_from_trail(tmp_path):
    """The wired exporters: trail-based, engagement-scoped, ONE
    overwriting file each (no timestamped copies, no workspace-root
    strays)."""
    at.set_audit_dir(tmp_path)
    at.start_audit("export probe")
    at.log_finding("critical", "sqli", "/login", "login bypass", "admin row returned")
    at.log_iteration(1, "t", "", "http_request", {"path": "/login"}, "200 admin row", True, "exploitation")
    from suijin.modules.tools.lib.reporting import _export_burp, _export_html

    h = _export_html("export probe")
    b = _export_burp("export probe")
    assert "Error" not in h and "report.html" in h, h
    assert "Error" not in b and "burp_export.xml" in b, b
    # overwrite policy: a second call does not create a second file
    _export_html("export probe")
    import pathlib

    files = list(pathlib.Path(h.split(": ")[1]).parent.glob("*.html"))
    assert len(files) == 1


def test_completion_turn_does_not_erase_the_real_tool(tmp_path):
    """Field-found on the live run: iteration N executes http_request,
    then the SAME iteration number logs the completion turn (tool=none,
    fresh thought). The merge must keep the real tool+args+obs AND the
    completion thought — 'none' is not content."""
    at.set_audit_dir(tmp_path)
    at.start_audit("merge probe")
    at.log_iteration(4, "probe files surface", "", "http_request", {"url": "http://t/files/public/"}, "404 not found", True, "exploitation")
    at.log_iteration(4, "Objective met — completing with summary", "", "", {}, "", True, "informational", completion_reason="objective complete")
    live = at.current_trail("merge probe")
    its = live["iterations"]
    assert len(its) == 1
    it = its[0]
    assert it["action"]["tool"] == "http_request", it["action"]
    assert it["action"]["args"]["url"].endswith("/files/public/")
    assert it["observation"] == "404 not found"
    assert "Objective met" in it["thought"]
    assert it["completion_reason"] == "objective complete"
