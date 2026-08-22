"""Subagent system end-to-end — deploy -> analyze (AI + fallback) -> route
traffic -> aggregate. The AI provider is mocked; the fallback path runs for
real (that's the no-API-key experience)."""

import asyncio
import json

from suijin.modules.blueteam.lib.blue.subagent_manager import SubagentManager


def make_mgr(tmp_path):
    return SubagentManager({}, str(tmp_path))


def sample_ai_json(sa):
    return json.dumps(
        {
            "risk_score": 8,
            "vulnerability_notes": "Raw SQL concatenation in login",
            "honeypot_code": "def honeypot(): ...",
            "patch_code": "db.execute('SELECT ... WHERE u=?', (u,))",
            "deception_response": '{"fake_users": []}',
            "defense_plan": "parameterize + rate limit",
            "normal_patterns": ["POST", "username+password"],
        }
    )


class TestAnalyzeEndpoint:
    def test_ai_path_populates_everything(self, tmp_path, monkeypatch):
        from suijin.modules.providers import lib as _providers

        mgr = make_mgr(tmp_path)
        sa = mgr.deploy_all([{"path": "/login", "method": "POST", "file": __file__}])[0]
        captured = {}

        def fake_generate(messages, config=None, **kw):
            captured["prompt"] = messages[-1]["content"]
            return sample_ai_json(sa)

        monkeypatch.setattr(_providers, "generate", fake_generate)
        asyncio.run(mgr.analyze_endpoint(sa))

        assert sa.status == "active"
        assert sa.risk_score == 8
        assert "Raw SQL" in sa.vulnerability_notes
        assert sa.patch_code.startswith("db.execute")
        assert sa.honeypot_code and sa.deception_response
        assert sa.normal_patterns == ["POST", "username+password"]
        # prompt carries the real handler source + endpoint metadata
        assert "/login" in captured["prompt"]
        assert "FULL HANDLER CODE" in captured["prompt"]

    def test_ai_failure_falls_back(self, tmp_path, monkeypatch):
        from suijin.modules.providers import lib as _providers

        mgr = make_mgr(tmp_path)
        sa = mgr.deploy_all([{"path": "/admin/config", "method": "GET", "file": __file__}])[0]

        def dead(messages, config=None, **kw):
            raise RuntimeError("no API key")

        monkeypatch.setattr(_providers, "generate", dead)
        asyncio.run(mgr.analyze_endpoint(sa))

        assert sa.status == "active"  # still deployable
        # fallback analysis REPLACES the failure note with real intel.
        # __file__ contains request.args/eval-ish patterns from test source,
        # so score lands at 8 (injection patterns) — the point is: fallback
        # RAN and produced intel, and /admin lifted it off the floor (>=5).
        assert "risk detected" in sa.vulnerability_notes or "Sensitive endpoint" in sa.vulnerability_notes
        assert sa.risk_score >= 5
        assert sa.defense_plan  # generic plan present

    def test_fallback_flags_command_injection(self, tmp_path, monkeypatch):
        from suijin.modules.providers import lib as _providers

        mgr = make_mgr(tmp_path)
        src = tmp_path / "ping_handler.py"
        src.write_text("import os\nos.system('ping ' + request.args['host'])\n")
        sa = mgr.deploy_all([{"path": "/ping", "method": "GET", "file": str(src)}])[0]

        monkeypatch.setattr(_providers, "generate", lambda m, c=None, **k: (_ for _ in ()).throw(RuntimeError("down")))
        asyncio.run(mgr.analyze_endpoint(sa))
        assert sa.risk_score >= 8
        assert "Command injection" in sa.vulnerability_notes

    def test_fallback_flags_sqli_patterns(self, tmp_path, monkeypatch):
        from suijin.modules.providers import lib as _providers

        mgr = make_mgr(tmp_path)
        src = tmp_path / "search_handler.py"
        src.write_text("q = f\"SELECT * FROM t WHERE x='{request.args['q']}'\"\n")
        sa = mgr.deploy_all([{"path": "/search", "method": "GET", "file": str(src)}])[0]

        monkeypatch.setattr(_providers, "generate", lambda m, c=None, **k: (_ for _ in ()).throw(RuntimeError("down")))
        asyncio.run(mgr.analyze_endpoint(sa))
        assert sa.risk_score >= 7
        assert "SQLi" in sa.vulnerability_notes


class TestBatching:
    def test_analyze_all_batches_and_collects(self, tmp_path, monkeypatch):
        import suijin.modules.blueteam.lib.blue.subagent_manager as sm
        from suijin.modules.providers import lib as _providers

        mgr = make_mgr(tmp_path)
        eps = [{"path": f"/p{i}", "method": "GET", "file": __file__} for i in range(7)]
        mgr.deploy_all(eps)
        monkeypatch.setattr(_providers, "generate", lambda m, c=None, **k: sample_ai_json(None))
        # shrink the inter-batch sleep so the test is fast
        real_sleep = sm.asyncio.sleep
        monkeypatch.setattr(sm.asyncio, "sleep", lambda s: real_sleep(0))
        results = asyncio.run(mgr.analyze_all_endpoints())
        assert len(results) == 7
        assert all(r.status == "active" for r in results)

    def test_batch_exception_does_not_kill_others(self, tmp_path, monkeypatch):

        mgr = make_mgr(tmp_path)
        eps = [{"path": f"/x{i}", "method": "GET", "file": __file__} for i in range(3)]
        deployed = mgr.deploy_all(eps)

        # endpoint 2 explodes inside analyze (not the provider — deeper)
        async def poisoned(sa):
            if sa is deployed[1]:
                sa.handler_code = None
                raise RuntimeError("boom")
            sa.status = "active"
            return sa

        monkeypatch.setattr(mgr, "analyze_endpoint", poisoned)
        results = asyncio.run(mgr.analyze_all_endpoints())
        assert len(results) == 2  # survivors collected, exception dropped


class TestTrafficRouting:
    def test_record_anomaly_and_block_counting(self, tmp_path):
        mgr = make_mgr(tmp_path)
        mgr.deploy_all([{"path": "/login", "method": "POST"}])
        mgr.record_anomaly("/login", "ANOMALOUS")
        mgr.record_anomaly("/login", "FLAGGED")
        mgr.record_anomaly("/login", "FLAGGED")
        mgr.record_anomaly("/other", "FLAGGED")  # no owner — silently ignored
        s = mgr.get_summary()
        assert s["total_anomalies"] == 3
        assert s["total_blocked"] == 2

    def test_notes_render_for_request(self, tmp_path):
        mgr = make_mgr(tmp_path)
        sa = mgr.deploy_all([{"path": "/api/users/<int:uid>", "method": "GET"}])[0]
        sa.risk_score = 9
        sa.vulnerability_notes = "IDOR: no ownership check"
        notes = mgr.get_subagent_notes("/api/users/42")
        assert "Risk Score: 9/10" in notes
        assert "IDOR" in notes

    def test_notes_empty_for_unknown(self, tmp_path):
        mgr = make_mgr(tmp_path)
        assert mgr.get_subagent_notes("/nothing") == ""


class TestSummary:
    def test_summary_risk_ordering(self, tmp_path):
        mgr = make_mgr(tmp_path)
        a = mgr.deploy_all(
            [{"path": "/low", "method": "GET"}, {"path": "/high", "method": "GET"}, {"path": "/mid", "method": "GET"}]
        )
        a[1].risk_score = 9
        a[2].risk_score = 5
        s = mgr.get_summary()
        assert s["by_risk"][0]["path"] == "/high"
        assert s["high_risk"] == 1
        assert s["active"] == 0  # none analyzed yet

    def test_parse_json_variants(self):
        assert SubagentManager._parse_json('{"a": 1}') == {"a": 1}
        assert SubagentManager._parse_json('noise before {"a": 2} noise after') == {"a": 2}
        assert SubagentManager._parse_json("not json at all") == {}
        assert SubagentManager._parse_json("") == {}


class TestIntegrationWithFeed:
    """The blueteamer wiring consumes deploy_all + analyze_all + summary —
    verify the exact sequence it calls holds together."""

    def test_blueteamer_sequence(self, tmp_path, monkeypatch):
        import suijin.modules.blueteam.lib.blue.subagent_manager as sm
        from suijin.modules.providers import lib as _providers

        mgr = make_mgr(tmp_path)
        endpoints = [{"path": p, "method": "GET", "file": __file__} for p in ("/", "/login", "/admin", "/api/x")]
        deployed = mgr.deploy_all(endpoints)
        assert len(deployed) == 4

        monkeypatch.setattr(
            _providers, "generate", lambda m, c=None, **k: json.dumps({"risk_score": 3, "vulnerability_notes": "ok"})
        )
        real_sleep = sm.asyncio.sleep
        monkeypatch.setattr(sm.asyncio, "sleep", lambda s: real_sleep(0))
        analyzed = asyncio.run(mgr.analyze_all_endpoints())
        assert len(analyzed) == 4

        # live traffic phase
        for path, verdict in (("/login", "FLAGGED"), ("/admin", "FLAGGED"), ("/", "ANOMALOUS")):
            mgr.record_anomaly(path, verdict)
        s = mgr.get_summary()
        assert s["total"] == 4 and s["active"] == 4
        assert s["high_risk"] == 0
        assert s["total_blocked"] == 2
        assert mgr.get_subagent_notes("/admin") != ""
