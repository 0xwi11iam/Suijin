"""Wave 4: C19-C26 — templates, language, engagement templates, schedule,
webhook, portal, fork, theater."""

import json
from unittest import mock

from suijin.modules.ops.lib import engagement_templates as et
from suijin.modules.ops.lib.notify import send_webhook
from suijin.modules.tools.lib import portal, report_templates, theater


def _findings():
    return [
        {
            "type": "sqli",
            "severity": "critical",
            "confidence": "verified",
            "evidence": "' OR 1=1 --",
            "target": "t.example",
        },
        {
            "type": "xss",
            "severity": "medium",
            "confidence": "suspected",
            "evidence": "<script>1</script>",
            "target": "t.example",
        },
    ]


class TestReportTemplates:
    def test_exec_one_pager(self):
        out = report_templates.render(_findings(), "eng", "exec")
        assert "Executive Summary" in out and "HIGH RISK" in out and "2 findings total" in out

    def test_technical_detail(self):
        out = report_templates.render(_findings(), "eng", "technical")
        assert "verification:" in out and "' OR 1=1" in out

    def test_compliance_map(self):
        out = report_templates.render(_findings(), "eng", "compliance")
        assert "CWE" in out and "|" in out

    def test_languages(self):
        de = report_templates.render(_findings(), "e", "exec", language="de")
        assert "Zusammenfassung" in de
        fr = report_templates.render([], "e", "exec", language="fr")
        assert "Aucune constatation" in fr

    def test_unknown(self):
        assert "unknown template" in report_templates.render([], "e", "bogus")


class TestEngagementTemplates:
    def test_list_and_apply(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        assert "external_web" in et.list_templates()
        resolved = et.apply_template("external_web", "https://acme.com")
        assert resolved["target"] == "https://acme.com"
        assert "acme.com" in json.dumps(resolved["policy"])

    def test_save_user_template(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        assert "saved" in et.save_template("nightly", {"objective": "x {target}"})
        assert "Error" in et.save_template("external_web", {})
        assert et.apply_template("nightly", "t")["objective"] == "x t"

    def test_schedule_dry_run(self):
        out = et.schedule_engagement("external_web", "0 3 * * *", "https://acme.com")
        assert out["installed"] is False and "0 3 * * *" in out["entry"] and "# suijin:" in out["entry"]


class TestWebhook:
    def test_post(self):
        with mock.patch("requests.post") as p:
            p.return_value.status_code = 200
            p.return_value.content = b"ok"
            assert send_webhook("https://hooks/x", {"text": "found"}) == "webhook 200 (2B)"

    def test_no_url(self):
        assert send_webhook("", {}).startswith("Error")


class TestPortal:
    def test_export_html(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        out = portal.export_portal(_findings(), "client eng")
        assert out.exists() and "Suijin Assessment" in out.read_text()
        assert "OR 1=1" in out.read_text()  # evidence present


class TestFork:
    def test_fork_truncates(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        from suijin.modules.tools.lib import session_replay as sr

        src = tmp_path / "s.json"
        src.write_text(
            json.dumps(
                {
                    "thread_id": "orig",
                    "objective": "o",
                    "state_summary": {"iterations": 5},
                    "iterations": [{"iteration": i, "tool": f"t{i}", "success": True} for i in range(1, 6)],
                }
            )
        )
        fork = sr.fork_from_iteration(src, 3)
        data = json.loads(fork.read_text())
        assert data["forked_at_iteration"] == 3 and len(data["iterations"]) == 3
        assert "forked @it3" in data["objective"]


class TestTheater:
    def test_frames(self):
        session = {
            "iterations": [
                {"iteration": 1, "tool": "nmap", "success": True, "thought": "scan"},
                {"iteration": 2, "tool": "sqli", "success": False, "thought": "inject"},
            ]
        }
        frames = theater.render_frames(session, width=10)
        assert len(frames) == 2 and "OK  nmap" in frames[0] and "FAIL sqli" in frames[1]
