"""Coverage push: session_control (runtime commands), codebase scanners
(endpoint discovery), subagent_manager (blue-team per-endpoint agents)."""

import json

import pytest

from suijin.modules.redteam.lib.red import session_control as sc


class TestBuildAttackChains:
    def test_chains_split_on_completion(self):
        trace = [
            {"tool_name": "nmap", "success": True},
            {"tool_name": "sqlmap", "success": True, "completion_reason": "flag"},
            {"tool_name": "gobuster", "success": False},
        ]
        chains = sc.build_attack_chains(trace)
        assert len(chains) == 2
        assert chains[0]["steps"] == ["nmap (OK)", "sqlmap (OK)"]
        assert chains[1]["steps"] == ["gobuster (FAIL)"]

    def test_empty_trace(self):
        assert sc.build_attack_chains([]) == []


class TestObjectiveFile:
    def test_txt_load(self, tmp_path, capsys):
        f = tmp_path / "obj.txt"
        f.write_text("  own the lab  \n")
        assert sc.load_objective_from_file(str(f)) == "own the lab"

    def test_drag_drop_quotes_stripped(self, tmp_path):
        f = tmp_path / "obj.md"
        f.write_text("objective text")
        assert sc.load_objective_from_file(f'"{f}"') == "objective text"

    def test_missing_file(self, capsys):
        assert sc.load_objective_from_file("/nope/nowhere.txt") is None
        assert "not found" in capsys.readouterr().out

    def test_directory_rejected(self, tmp_path, capsys):
        assert sc.load_objective_from_file(str(tmp_path)) is None

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("   ")
        assert sc.load_objective_from_file(str(f)) is None

    def test_rtf_regex_fallback(self, tmp_path, monkeypatch):
        import builtins

        f = tmp_path / "doc.rtf"
        f.write_text(r"{\rtf1\ansi hello {\b world}}")
        # force the ImportError fallback path (no striprtf)
        real_import = builtins.__import__

        def no_rtf(name, *a, **k):
            if "striprtf" in name:
                raise ImportError(name)
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", no_rtf)
        out = sc.load_objective_from_file(str(f))
        assert out is not None and "hello" in out and "{" not in out


class TestPythonAnalyzer:
    def test_flask_fastapi_django_extraction(self, tmp_path):
        from suijin.modules.blueteam.lib.blue.codebase.python_analyzer import extract_python_routes

        (tmp_path / "app.py").write_text(
            "from flask import Flask\napp = Flask(__name__)\n"
            "@app.route('/login')\ndef login(): pass\n"
            "@app.get('/api/x')\ndef x(): pass\n"
            "@router.post('/items')\ndef items(): pass\n"
            "from django.urls import path\n"
            "urlpatterns = [path('admin/', admin.site.urls)]\n"
        )
        routes = extract_python_routes(tmp_path)
        by_fw = {}
        for r in routes:
            by_fw.setdefault(r["framework"], []).append(r)
        assert by_fw["flask"][0]["path"] == "/login"
        fastapi = {r["path"]: r["method"] for r in by_fw.get("fastapi", [])}
        assert fastapi == {"/api/x": "GET", "/items": "POST"}
        assert by_fw["django"][0]["view"] == "admin"

    def test_excludes_noise_dirs(self, tmp_path):
        from suijin.modules.blueteam.lib.blue.codebase.python_analyzer import extract_python_routes

        noise = tmp_path / "__pycache__"
        noise.mkdir()
        (noise / "mod.py").write_text("@app.route('/x')\ndef x(): pass\n")
        assert extract_python_routes(tmp_path) == []


class TestJsAnalyzer:
    def test_express_routes(self, tmp_path):
        from suijin.modules.blueteam.lib.blue.codebase.javascript_analyzer import extract_js_routes

        (tmp_path / "server.js").write_text("app.get('/health', handler)\nrouter.post('/api/users', h)\n")
        routes = extract_js_routes(tmp_path)
        found = {(r["method"], r["path"]) for r in routes}
        assert found == {("GET", "/health"), ("POST", "/api/users")}
        assert all(r["framework"] == "express" for r in routes)


class TestScanCodebase:
    def test_scan_writes_summary_and_merges(self, tmp_path):
        from suijin.modules.blueteam.lib.blue.codebase.scanner import scan_codebase

        (tmp_path / "a.py").write_text("@app.route('/a')\ndef a(): pass\n")
        (tmp_path / "b.js").write_text("app.get('/b', h)\n")
        endpoints = scan_codebase(str(tmp_path))
        paths = {e["path"] for e in endpoints}
        assert paths == {"/a", "/b"}
        summary = json.loads((tmp_path / "suijin_endpoints.json").read_text())
        assert len(summary) == 2


class TestSubagentManager:
    @pytest.fixture
    def mgr(self):
        from suijin.modules.blueteam.lib.blue.subagent_manager import SubagentManager

        return SubagentManager({}, "/tmp")

    def _deploy(self, mgr, *paths):
        return mgr.deploy_all([{"path": p, "method": "GET"} for p in paths])

    def test_deploy_all_one_per_endpoint(self, mgr):
        deployed = self._deploy(mgr, "/login", "/admin", "/api/users")
        assert len(deployed) == 3
        assert [sa.rank for sa in deployed] == [1, 2, 3]
        assert {sa.agent_id for sa in deployed} == {"subagent-01", "subagent-02", "subagent-03"}
        # one subagent per unique path (md5-keyed, idempotent re-deploy)
        again = mgr.deploy_all([{"path": "/login", "method": "GET"}])
        assert again[0].agent_id == "subagent-01"

    def test_find_exact_and_prefix_match(self, mgr):
        self._deploy(mgr, "/api/users", "/login")
        assert mgr.find_for_request("/api/users").endpoint["path"] == "/api/users"
        # prefix match: concrete id under a parameterized parent
        mgr2 = type(mgr)({}, "/tmp")
        mgr2.deploy_all([{"path": "/api/users/<int:uid>", "method": "GET"}])
        hit = mgr2.find_for_request("/api/users/42")
        assert hit is not None

    def test_find_miss_returns_none(self, mgr):
        self._deploy(mgr, "/login")
        assert mgr.find_for_request("/totally/other") is None

    def test_subagent_dataclass_defaults(self, mgr):
        sa = self._deploy(mgr, "/x")[0]
        assert sa.risk_score == 1 and sa.status == "initializing"
        assert sa.anomalies_reported == 0 and sa.attacks_blocked == 0
        assert sa.handler_code == ""  # filled during analyze_endpoint
