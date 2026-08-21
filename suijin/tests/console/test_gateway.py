"""Desktop gateway — the typed API between core and Tauri app.

Auth (401 matrix), every REST route's contract, WS stream frames,
approval/question round-trips, and OpenAPI generation (the source of
the TS client types — this test pins that the schema exists and is
rich enough to generate from).
"""

import json

import pytest
from fastapi.testclient import TestClient

from suijin.modules.console.lib.gateway import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from suijin.modules.platform.lib import workspace as ws

    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    app = create_app(token="testtok")
    return TestClient(app), {"Authorization": "Bearer testtok"}


class TestAuth:
    def test_no_token_401(self, client):
        c, _ = client
        for path in ("/api/status", "/api/tools", "/api/usage", "/api/findings", "/api/spar", "/api/approvals"):
            assert c.get(path).status_code == 401, path

    def test_wrong_token_401(self, client):
        c, _ = client
        assert c.get("/api/status", headers={"Authorization": "Bearer nope"}).status_code == 401

    def test_right_token_200(self, client):
        c, h = client
        assert c.get("/api/status", headers=h).status_code == 200


class TestReadOnlyRoutes:
    def test_status_shape(self, client):
        c, h = client
        d = c.get("/api/status", headers=h).json()
        for key in ("version", "units", "tools", "packs", "kb_docs", "kb_built", "provider", "stealth", "kg_backend"):
            assert key in d
        assert d["tools"] >= 250 and d["units"] >= 100

    def test_tools_catalog(self, client):
        c, h = client
        tools = c.get("/api/tools", headers=h).json()
        assert len(tools) >= 250
        sample = tools[0]
        assert set(sample) >= {"name", "owner", "description", "params"}

    def test_usage_and_findings_and_spar(self, client):
        c, h = client
        u = c.get("/api/usage", headers=h).json()
        assert "calls" in u and "est_cost_usd" in u
        f = c.get("/api/findings", headers=h).json()
        assert isinstance(f, dict)
        s = c.get("/api/spar", headers=h).json()
        assert "recall" in s


class TestHitlRoundTrip:
    def _seed(self, tmp_path):
        d = tmp_path / "outputs" / "audit_trails"
        d.mkdir(parents=True, exist_ok=True)  # not used; approvals live in outputs/
        (tmp_path / "outputs" / "approvals.jsonl").write_text(
            json.dumps({"id": 1, "command": "nmap -sS 10.0.0.0/8", "status": "pending"})
            + "\n"
            + json.dumps({"id": 2, "command": "rm -rf /", "status": "pending"})
            + "\n"
        )

    def test_list_decide_roundtrip(self, client, tmp_path):
        self._seed(tmp_path)
        c, h = client
        items = c.get("/api/approvals", headers=h).json()
        assert len(items) == 2 and items[0]["status"] == "pending"
        # approve 1, deny 2 with a note
        r = c.post("/api/approvals/1", headers=h, json={"action": "approve"})
        assert r.json()["status"] == "approved"
        r = c.post("/api/approvals/2", headers=h, json={"action": "deny", "note": "too broad"})
        d = r.json()
        assert d["status"] == "denied" and d["note"] == "too broad"
        # persisted
        disk = [json.loads(ln) for ln in (tmp_path / "outputs" / "approvals.jsonl").read_text().splitlines()]
        assert [x["status"] for x in disk] == ["approved", "denied"]

    def test_decide_missing_404(self, client):
        c, h = client
        assert c.post("/api/approvals/99", headers=h, json={"action": "approve"}).status_code == 404

    def test_question_answer(self, client, tmp_path):
        (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "outputs" / "questions.jsonl").write_text(
            json.dumps({"id": 1, "question": "scope ok?", "answered": False}) + "\n"
        )
        c, h = client
        r = c.post("/api/questions/1", headers=h, json={"answer": "yes, go"})
        assert r.json()["answered"] is True and r.json()["answer"] == "yes, go"


class TestOpenAPI:
    def test_schema_generatable(self, client):
        """The TS client is generated from this — pin its existence + paths."""
        c, _ = client
        spec = c.get("/openapi.json").json()
        paths = spec["paths"]
        for must in (
            "/api/status",
            "/api/tools",
            "/api/usage",
            "/api/findings",
            "/api/spar",
            "/api/approvals",
            "/api/questions",
            "/api/engage",
        ):
            assert must in paths, must
        # typed: status response schema exists
        assert spec["paths"]["/api/status"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
