"""Tests for export / debrief / replay — the artifact tooling trio."""

import json
import zipfile

import pytest

from suijin.tools import debrief as db
from suijin.tools import replay as rp
from suijin.tools.export_bundle import build_bundle, verify_bundle


@pytest.fixture
def ws(tmp_path):
    """Workspace with a full engagement's worth of artifacts + KGs + config."""
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "eng.md").write_text("# report\nFLAG{x}")
    (tmp_path / "audit_trails").mkdir()
    trail = {
        "engagement": "testlab",
        "started": "2026-08-17T10:00:00+00:00",
        "ended": "2026-08-17T10:05:00+00:00",
        "total_actions": 3,
        "successful_actions": 2,
        "failed_actions": 1,
        "cost_usd": 0.5,
        "findings": [{"severity": "high"}, {"severity": "low"}],
        "iterations": [
            {
                "iteration": 1,
                "phase": "recon",
                "timestamp": "2026-08-17T10:00:10+00:00",
                "thought": "scan first",
                "action": {"tool": "nmap_scan", "args": {"target": "x"}, "success": True},
                "observation": "22/tcp open",
            },
            {
                "iteration": 2,
                "phase": "exploit",
                "timestamp": "2026-08-17T10:01:00+00:00",
                "thought": "try sqli",
                "action": {"tool": "http_request", "args": {}, "success": True},
                "observation": "FLAG{x}",
            },
            {
                "iteration": 3,
                "phase": "exploit",
                "timestamp": "2026-08-17T10:02:00+00:00",
                "thought": "again",
                "action": {"tool": "http_request", "args": {}, "success": False},
                "observation": "403",
            },
        ],
    }
    (tmp_path / "audit_trails" / "testlab.json").write_text(json.dumps(trail))
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "s1.json").write_text(json.dumps({"objective": "o"}))
    (tmp_path / "credentials.json").write_text('{"api_key": "sk-supersecret"}')
    red_kg = tmp_path.parent / "intel" / "knowledge_graph.json"
    red_kg.parent.mkdir(parents=True, exist_ok=True)
    red_kg.write_text('{"10.0.0.1": {"blocks": []}}')
    cfg = tmp_path.parent / "config.json"
    cfg.write_text(json.dumps({"provider": "zai", "api_key": "sk-live", "zai_endpoint": "coding"}))
    return {"ws": tmp_path, "red_kg": red_kg, "cfg": cfg}


# ── export ─────────────────────────────────────────────────────────────


class TestExport:
    def test_build_and_verify(self, ws):
        out = build_bundle(workspace=ws["ws"], red_kg_path=ws["red_kg"], config_path=ws["cfg"])
        assert out.exists() and out.suffix == ".zip"
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        assert "manifest.json" in names and "custody.json" in names
        assert "workspace/reports/eng.md" in names
        assert "workspace/audit_trails/testlab.json" in names
        assert "config.redacted.json" in names
        ok, problems = verify_bundle(out)
        assert ok, problems

    def test_credentials_excluded_by_default(self, ws):
        out = build_bundle(workspace=ws["ws"], red_kg_path=ws["red_kg"], config_path=ws["cfg"])
        with zipfile.ZipFile(out) as zf:
            assert "workspace/credentials.json" not in zf.namelist()

    def test_credentials_opt_in(self, ws):
        out = build_bundle(
            workspace=ws["ws"], red_kg_path=ws["red_kg"], config_path=ws["cfg"], include_credentials=True
        )
        with zipfile.ZipFile(out) as zf:
            assert "workspace/credentials.json" in zf.namelist()

    def test_config_redacted_in_bundle(self, ws):
        out = build_bundle(workspace=ws["ws"], red_kg_path=ws["red_kg"], config_path=ws["cfg"])
        with zipfile.ZipFile(out) as zf:
            body = zf.read("config.redacted.json").decode()
        assert "sk-live" not in body
        assert "***redacted***" in body
        assert '"provider"' in body

    def test_tamper_detected(self, ws):
        out = build_bundle(workspace=ws["ws"], red_kg_path=ws["red_kg"], config_path=ws["cfg"])
        # rewrite the zip with one file's contents changed
        src = zipfile.ZipFile(out)
        items = [(i, src.read(i.filename)) for i in src.infolist()]
        src.close()
        tampered = out.with_name("tampered.zip")
        with zipfile.ZipFile(tampered, "w") as zf:
            for info, data in items:
                if info.filename == "workspace/reports/eng.md":
                    data = b"# tampered report"
                zf.writestr(info, data)
        ok, problems = verify_bundle(tampered)
        assert not ok
        assert any("eng.md" in p for p in problems)

    def test_extra_file_detected(self, ws):
        out = build_bundle(workspace=ws["ws"], red_kg_path=ws["red_kg"], config_path=ws["cfg"])
        src = zipfile.ZipFile(out)
        items = [(i, src.read(i.filename)) for i in src.infolist()]
        src.close()
        extra = out.with_name("extra.zip")
        with zipfile.ZipFile(extra, "w") as zf:
            for info, data in items:
                zf.writestr(info, data)
            zf.writestr("sneaky.txt", b"x")
        ok, problems = verify_bundle(extra)
        assert not ok
        assert any("sneaky" in p for p in problems)


# ── debrief ────────────────────────────────────────────────────────────


class TestDebrief:
    def test_load_and_stats(self, ws):
        trails = db.load_audits(ws["ws"] / "audit_trails")
        assert len(trails) == 1
        s = db.engagement_stats(trails[0])
        assert s["engagement"] == "testlab"
        assert s["actions"] == 3 and s["success"] == 2 and s["failed"] == 1
        assert s["findings"] == 2
        assert s["findings_by_severity"] == {"HIGH": 1, "LOW": 1}
        assert s["duration_s"] == 300.0
        assert s["tools"]["http_request"] == 2

    def test_fleet_trends(self, ws):
        trails = db.load_audits(ws["ws"] / "audit_trails")
        f = db.fleet_stats(trails)
        assert f["engagements"] == 1
        assert f["total_findings"] == 2
        assert f["total_cost_usd"] == 0.5
        assert f["avg_duration_s"] == 300.0

    def test_render(self, ws):
        trails = db.load_audits(ws["ws"] / "audit_trails")
        out = db.render_debrief(trails, verbose=True)
        assert "ENGAGEMENTS (1)" in out
        assert "testlab" in out
        assert "FLEET TRENDS" in out
        assert "http_request" in out  # verbose: tool breakdown

    def test_empty(self, tmp_path):
        assert "No audit trails" in db.render_debrief(db.load_audits(tmp_path))


# ── replay ─────────────────────────────────────────────────────────────


class TestReplay:
    def test_list_replays(self, ws):
        trails = rp.list_replays(ws["ws"] / "audit_trails")
        assert len(trails) == 1
        assert len(trails[0]["iterations"]) == 3

    def test_empty_trails_excluded(self, ws):
        (ws["ws"] / "audit_trails" / "empty.json").write_text(json.dumps({"engagement": "e", "iterations": []}))
        trails = rp.list_replays(ws["ws"] / "audit_trails")
        assert len(trails) == 1

    def test_markdown_transcript(self, ws):
        trail = rp.list_replays(ws["ws"] / "audit_trails")[0]
        md = rp.render_markdown(trail)
        assert "# Replay — testlab" in md
        assert "## Step 1 — recon [✔]" in md
        assert "## Step 3 — exploit [✘]" in md
        assert "nmap_scan" in md
        assert "FLAG{x}" in md

    def test_non_tty_replay_prints_transcript(self, ws, capsys, monkeypatch):
        trail = rp.list_replays(ws["ws"] / "audit_trails")[0]
        monkeypatch.setattr(rp.sys.stdin, "isatty", lambda: False)
        rp.run_replay(trail)
        out = capsys.readouterr().out
        assert "# Replay — testlab" in out
