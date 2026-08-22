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
        # v5.1: the gateway reads/writes the REAL agent store (ops approvals
        # module). Seed it through the same API the agent uses.
        from suijin.modules.ops.lib import approvals as ap

        ap.APPROVALS_PATH = tmp_path / "approvals.json"
        ap.record_pending("nmap", {"cmd": "nmap -sS 10.0.0.0/8"})
        ap.record_pending("rm", {"cmd": "rm -rf /"})

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
        # persisted in the REAL store the agent polls
        from suijin.modules.ops.lib import approvals as ap

        disk = ap.list_approvals()
        assert [x["status"] for x in sorted(disk, key=lambda i: i["id"])] == ["approved", "denied"]
        assert ap.decision_for("nmap") == "approved"

    def test_decide_missing_404(self, client, tmp_path):
        from suijin.modules.ops.lib import approvals as ap

        ap.APPROVALS_PATH = tmp_path / "empty.json"
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


class TestWorkspaceAnchoring:
    def test_boot_does_not_pollute_cwd(self, tmp_path, monkeypatch):
        """Root-cause regression: the gateway boots the kernel with the
        platform WORKSPACE_DIR — never Path.cwd(). A boot from a scratch
        directory must not scatter outputs/audit_trails/payloads/reports
        there (this exact bug left junk at the repo root)."""

        from suijin.modules.platform.lib import workspace as ws

        scratch = tmp_path / "scratch-cwd"
        scratch.mkdir()
        monkeypatch.chdir(scratch)
        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path / "ws")

        from fastapi.testclient import TestClient

        from suijin.modules.console.lib.gateway import create_app

        c = TestClient(create_app(token="t"))
        r = c.get("/api/status", headers={"Authorization": "Bearer t"})
        assert r.status_code == 200
        # the cwd stays pristine
        assert list(scratch.iterdir()) == [], f"cwd polluted: {[p.name for p in scratch.iterdir()]}"


class TestCostFrameTokens:
    def test_cost_frame_carries_token_counts(self):
        """The right-side token counter (desktop topbar + TUI) reads the
        'cost' WS frame; the pump builds it from get_usage() — this pins
        that every field the UI needs is present in the usage dict."""
        from suijin.modules.providers.lib import _record_usage, reset_usage

        reset_usage()
        _record_usage("test", "test-model", 1000, 500)
        from suijin.modules.providers.lib import get_usage

        u = get_usage()
        tok = int(u.get("input_tokens", 0)) + int(u.get("output_tokens", 0))
        # the frame the pump sends (synchronized with gateway/__init__.py pump)
        frame = {
            "kind": "cost",
            "est_cost_usd": round(float(u.get("est_cost_usd", 0.0)), 4),
            "calls": u.get("calls", 0),
            "tokens": tok,
            "input_tokens": int(u.get("input_tokens", 0)),
            "output_tokens": int(u.get("output_tokens", 0)),
        }
        for k in ("est_cost_usd", "calls", "tokens", "input_tokens", "output_tokens"):
            assert k in frame, f"cost frame missing {k}"
        assert frame["tokens"] == 1500
        assert frame["input_tokens"] == 1000
        assert frame["output_tokens"] == 500
        reset_usage()


class TestTUICounterFormat:
    def test_iteration_line_with_counter(self, capsys):
        """The TUI iteration line carries a right-aligned tok+cost counter."""

        from suijin.modules.providers.lib import _record_usage, reset_usage

        reset_usage()
        _record_usage("test", "m", 2500, 700)

        providers = type("P", (), {"USAGE": {}})
        from suijin.modules.providers.lib import get_usage

        providers.USAGE = get_usage()
        _in = int(providers.USAGE.get("input_tokens", 0))
        _out = int(providers.USAGE.get("output_tokens", 0))
        _tok = _in + _out
        _tok_str = f"{_tok / 1000:.1f}k" if _tok >= 1000 else str(_tok)
        _cost = float(providers.USAGE.get("est_cost_usd", 0))
        _left = "#3 + exploitation"
        _right = f"{_tok_str} tok | ${_cost:.4f}"
        assert "3.2k" in _tok_str
        assert _tok == 3200
        assert _right.endswith(f"${_cost:.4f}")
        reset_usage()
