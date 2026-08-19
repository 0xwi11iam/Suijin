"""Tests for the WebUI backend (suijin/ui/server.py) — API contract, path
safety, redaction, SSE, traffic enrichment. All offline via Flask test client.
"""

import json

import pytest

from suijin.ui import server as uis


@pytest.fixture
def client():
    app = uis.create_app()
    app.config["TESTING"] = True
    return app.test_client()


class TestOverview:
    def test_overview_shape(self, client):
        r = client.get("/api/overview")
        assert r.status_code == 200
        d = r.get_json()
        for key in ("version", "provider", "kb", "labs", "traffic_count", "reports", "audits", "tools", "tarpit"):
            assert key in d, key

    def test_labs_include_real_labs_with_ports(self, client):
        d = client.get("/api/overview").get_json()
        by_name = {x["name"]: x for x in d["labs"]}
        assert by_name["blue_target"]["port"] == 5906
        assert by_name["devops_dashboard"]["port"] == 5700
        assert "running" in by_name["blue_target"]

    def test_provider_from_config(self, client):
        d = client.get("/api/overview").get_json()
        assert d["provider"]["name"]  # something is always reported

    def test_signal_counts_from_traffic(self, client, monkeypatch, tmp_path):

        log = tmp_path / "traffic.jsonl"
        log.write_text(
            "\n".join(
                json.dumps(e)
                for e in [
                    {"method": "GET", "path": "/", "body": ""},
                    {
                        "method": "POST",
                        "path": "/auth/login",
                        "body": "{\"u\":\"admin' OR '1'='1\"}",
                        "ip": "127.0.0.1",
                    },
                ]
            )
        )
        monkeypatch.setattr(uis, "BLUE_TRAFFIC_LOG", str(log))
        d = uis.build_snapshot()
        assert d["signal_counts"].get("sql_injection", 0) >= 1

    def test_attack_type_counts_from_blue_kg(self, client, monkeypatch, tmp_path):
        kg = tmp_path / "blue_kg.json"
        kg.write_text(
            json.dumps(
                {
                    "nodes": {
                        "a1": {
                            "id": "a1",
                            "type": "attack",
                            "data": {"attack_type": "sql_injection", "path": "/login"},
                        },
                        "a2": {
                            "id": "a2",
                            "type": "attack",
                            "data": {"attack_type": "sql_injection", "path": "/search"},
                        },
                        "d1": {"id": "d1", "type": "defense", "data": {}},
                    },
                    "edges": [],
                }
            )
        )
        monkeypatch.setattr(uis, "BLUE_KG_PATH", str(kg))
        d = uis.build_snapshot()
        assert d["blue_kg"]["attack_type_counts"]["sql_injection"] == 2
        assert d["blue_kg"]["node_counts"]["defense"] == 1


class TestPathSafety:
    def test_report_traversal_blocked(self, client):
        assert client.get("/api/report?path=../suijin/kb.py").status_code == 404
        assert client.get("/api/report?path=../../etc/passwd").status_code == 404
        assert client.get("/api/report?path=reports/../../../etc/passwd").status_code == 404

    def test_session_traversal_blocked(self, client):
        assert client.get("/api/session?file=../config.json").status_code == 404

    def test_static_traversal_blocked(self, client):
        assert client.get("/../../suijin/cli.py").status_code in (404, 403)

    def test_report_read_ok(self, client, tmp_path, monkeypatch):
        import pathlib

        monkeypatch.setattr(uis, "_workspace", lambda: pathlib.Path(tmp_path))
        (tmp_path / "reports").mkdir(parents=True)
        (tmp_path / "reports" / "r.md").write_text("# hello")
        r = client.get("/api/report?path=reports/r.md")
        assert r.status_code == 200
        assert r.get_json()["content"] == "# hello"


class TestRedaction:
    def test_config_secrets_redacted(self, client, monkeypatch):
        monkeypatch.setattr(
            "suijin.modules.redteam.lib.red.config_loader.load_config",
            lambda: {"provider": "zai", "api_key": "sk-live", "nested": {"hf_token": "t"}},
        )
        d = client.get("/api/config").get_json()
        assert d["api_key"] == "***redacted***"
        assert d["nested"]["hf_token"] == "***redacted***"
        assert "sk-live" not in json.dumps(d)


class TestKbSearch:
    def test_requires_q(self, client):
        assert client.get("/api/kb/search").status_code == 400

    def test_search_passes_through(self, client, monkeypatch):
        from suijin.modules.tools.lib import intel

        monkeypatch.setattr(intel, "search_kb", lambda q, limit=5: f"RESULT:{q}:{limit}")
        d = client.get("/api/kb/search?q=sqli&limit=3").get_json()
        assert d["result"] == "RESULT:sqli:3"


class TestDossierEndpoint:
    def test_requires_target(self, client):
        assert client.get("/api/dossier").status_code == 400

    def test_dossier_shape(self, client, monkeypatch):
        from suijin.modules.ops.lib import dossier as dos

        monkeypatch.setattr(
            dos,
            "build_dossier",
            lambda t: {
                "target": t,
                "constraints": {"blocks": ["x OR 1=1"]},
                "failures": [],
                "engagements": [],
                "reports": [],
            },
        )
        d = client.get("/api/dossier?target=10.0.0.5").get_json()
        assert d["target"] == "10.0.0.5"
        assert d["constraints"]["blocks"] == ["x OR 1=1"]

    def test_invalid_target_400(self, client, monkeypatch):
        from suijin.modules.ops.lib import dossier as dos

        def boom(t):
            raise ValueError("target required")

        monkeypatch.setattr(dos, "build_dossier", boom)
        assert client.get("/api/dossier?target=%20").status_code == 400


class TestTimelineEndpoint:
    def test_events_listed(self, client, monkeypatch):
        from suijin.modules.ops.lib import housekeeping as hk

        monkeypatch.setattr(
            hk,
            "build_timeline",
            lambda limit=60: [{"ts": "2026-08-18 01:00:00", "kind": "engagement start", "detail": "x"}],
        )
        d = client.get("/api/timeline").get_json()
        assert d["events"][0]["kind"] == "engagement start"

    def test_limit_clamped(self, client, monkeypatch):
        from suijin.modules.ops.lib import housekeeping as hk

        seen = {}

        def fake(limit=60):
            seen["limit"] = limit
            return []

        monkeypatch.setattr(hk, "build_timeline", fake)
        client.get("/api/timeline?limit=9999")
        assert seen["limit"] == 200


class TestKevInOverview:
    def test_kev_field_always_present(self, client, monkeypatch):
        import suijin.modules.knowledge.lib.cve_mirror as cm

        monkeypatch.setattr(cm, "kev_status", lambda: {"count": 1337, "retrieved": "x"})
        d = client.get("/api/overview").get_json()
        assert d["kev"]["count"] == 1337

    def test_kev_none_when_missing(self, client, monkeypatch):
        import suijin.modules.knowledge.lib.cve_mirror as cm

        monkeypatch.setattr(cm, "kev_status", lambda: None)
        d = client.get("/api/overview").get_json()
        assert d["kev"] == {"count": 0}


class TestSse:
    def test_events_stream_is_sse(self, client):
        # The generator is infinite by design — consume only the leading
        # snapshot frame, then close the response.
        r = client.get("/api/events", buffered=False)
        assert r.status_code == 200
        assert r.content_type.startswith("text/event-stream")
        chunks = r.response  # lazy generator
        first = next(chunks)
        assert first.startswith(b"data:")
        json.loads(first[len(b"data:") :])  # leading frame is a valid snapshot
        r.close()


class TestTrafficEnrichment:
    def test_sqli_entry_scores(self):
        entries = [
            {
                "method": "POST",
                "path": "/auth/login",
                "ip": "127.0.0.1",
                "body": '{"username":"admin\' OR \'1\'=\'1","password":"x"}',
            }
        ]
        out = uis._enrich_traffic(entries)
        assert out[0]["ui_score"] >= 4
        assert "sql_injection" in out[0]["ui_signals"]

    def test_clean_entry_scores_zero(self):
        out = uis._enrich_traffic([{"method": "GET", "path": "/", "body": ""}])
        assert out[0]["ui_score"] == 0

    def test_malformed_entries_kept(self):
        out = uis._enrich_traffic([{"method": "GET"}])
        assert out[0]["ui_score"] == 0


class TestStaticServing:
    def test_index_served(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert b'<div id="root">' in r.data

    def test_spa_fallback(self, client):
        r = client.get("/reports")
        assert r.status_code == 200
        assert b"root" in r.data

    def test_missing_asset_404(self, client):
        assert client.get("/assets/nope.js").status_code == 404
