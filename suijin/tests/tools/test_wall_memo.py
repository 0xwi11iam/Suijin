"""Wall-memo slices — the integrated tests (2026-11-03)."""

import json

import pytest


@pytest.fixture(autouse=True)
def _rows_ws(tmp_path, monkeypatch):
    """Rows live under <engagement>/state/rows/ — pin state_dir on BOTH
    module objects (the dual namespace creates two copies of every
    module; patching one leaves the other reading the real path)."""
    import suijin.modules.platform.lib.workspace as ws
    import suijin.modules.agent.lib.rows as rows_mod

    (tmp_path / "state").mkdir(parents=True)
    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(ws, "state_dir", lambda: tmp_path / "state", raising=False)
    # BOTH module objects — the shim namespace is a separate module
    monkeypatch.setattr(rows_mod, "rows_dir", lambda: tmp_path / "state" / "rows", raising=False)
    return tmp_path


class TestSlice2_Rows:
    def test_fact_survives_as_row(self, _rows_ws):
        from suijin.modules.agent.lib import rows

        fid = rows.record_fact("admin bypass works on /v1", "curl -H auth /v1 → 200 admin")
        assert fid.startswith("fa_")  # fact[:2] = fa
        assert any(f["id"] == fid for f in rows._all("fact"))
        assert "admin bypass" in rows.snapshot()

    def test_asset_idempotent(self, _rows_ws):
        from suijin.modules.agent.lib import rows

        a1 = rows.insert_asset("sub.example.com", "subdomain")
        a2 = rows.insert_asset("sub.example.com", "subdomain")
        assert a1 == a2  # same name → same id

    def test_intent_state_machine(self, _rows_ws):
        from suijin.modules.agent.lib import rows

        iid = rows.add_intent("exfil the token via imgres")
        assert rows.set_intent_state(iid, "exhausted", "budget")
        assert rows.open_intents() == []  # exhausted is not open
        assert any(i["id"] == iid for i in rows._all("intent"))

    def test_snapshot_lists_open_and_untested(self, _rows_ws):
        from suijin.modules.agent.lib import rows

        rows.insert_asset("api.example.com", "subdomain")
        rows.add_intent("steal the token")
        s = rows.snapshot()
        assert "open intents:" in s and "untested assets:" in s

    def test_rows_tools_dispatch(self, _rows_ws):
        from suijin.modules.tools.lib import dispatch

        out = dispatch.route_tool("record_fact", {"summary": "x works", "evidence": "cmd"}, config={})
        assert "fact" in str(out) and "recorded" in str(out)


class TestSlice3_Traffic:
    def test_record_and_search(self, _rows_ws, monkeypatch):
        import suijin.modules.tools.lib.traffic_index as ti
        monkeypatch.setattr(ti, "traffic_db", lambda: _rows_ws / "state" / "traffic" / "index.db")

        tid = ti.record_exchange("GET", "https://api.example.com/v1/x", 200, res_body='{"ok":true}')
        assert tid > 0
        out = ti.traffic_search(host="api.example.com")
        assert "#1" in out or f"#{tid}" in out

    def test_body_fts(self, _rows_ws, monkeypatch):
        import suijin.modules.tools.lib.traffic_index as ti
        monkeypatch.setattr(ti, "traffic_db", lambda: _rows_ws / "state" / "traffic" / "index.db")

        ti.record_exchange("GET", "https://a.example.com/x", 200, res_body="SECRET_MARKER_XYZ inside")
        out = ti.traffic_search(host="a.example.com", body="SECRET_MARKER_XYZ")
        assert "row" in out

    def test_get_returns_exchange(self, _rows_ws, monkeypatch):
        import suijin.modules.tools.lib.traffic_index as ti
        monkeypatch.setattr(ti, "traffic_db", lambda: _rows_ws / "state" / "traffic" / "index.db")

        tid = ti.record_exchange("POST", "https://b.example.com/login", 403, req_body="user=x")
        got = ti.traffic_get(tid)
        assert "POST" in got and "b.example.com" in got

    def test_host_required(self, _rows_ws, monkeypatch):
        import suijin.modules.tools.lib.traffic_index as ti
        monkeypatch.setattr(ti, "traffic_db", lambda: _rows_ws / "state" / "traffic" / "index.db")

        assert ti.traffic_search(host="").startswith("Error")


class TestSlice4_Planner:
    def test_planner_prompt_has_no_execution(self, _rows_ws, monkeypatch):
        import suijin.modules.agent.lib.planner as planner
        import suijin.modules.agent.lib.rows as rows_mod

        monkeypatch.setattr(rows_mod, "rows_dir", lambda: _rows_ws / "state" / "rows")
        p = planner.planner_system_prompt({})
        assert "do NOT execute" in p
        assert "emit_intents" in p
        assert "MUST emit" in p  # the empty-queue rule

    def test_parse_plan(self):
        from suijin.modules.agent.lib import planner

        plan = planner.parse_plan('{"emit_intents": [{"text": "steal token"}], "prove_goal": null}')
        assert len(plan["intents"]) == 1
        assert plan["prove"] is None

    def test_apply_plan_writes_rows(self, _rows_ws):
        from suijin.modules.agent.lib import planner
        from suijin.modules.agent.lib import rows as _r

        out = planner.apply_plan({"intents": [{"text": "exfil via chain"}], "prove": None})
        assert "1 intent" in out
        assert len(_r.open_intents()) == 1


class TestSlice5_Retest:
    def test_retest_confirmed(self, _rows_ws):
        from suijin.modules.agent.lib import rows
        from suijin.modules.tools.lib import retest

        rows.upsert_finding("fi_test1", "confirmed", "admin bypass on /v1", exchange_ids="none")
        out = retest.retest_finding("fi_test1")
        assert "RETEST" in out and "reproduced" in out

    def test_unknown_finding_lists_known(self, _rows_ws):
        from suijin.modules.tools.lib import retest

        out = retest.retest_finding("fi_nope")
        assert "not found" in out


class TestSlice1_Alternates:
    def test_missing_binary_names_alternate(self):
        from suijin.modules.tools.lib import alternates

        out = alternates.annotate_missing_binary("testssl.sh: not installed")
        assert "ALTERNATE" in out and "openssl" in out

    def test_normal_output_untouched(self):
        from suijin.modules.tools.lib import alternates

        assert alternates.annotate_missing_binary("Status: 200 OK") == "Status: 200 OK"


class TestSlice1_Drift:
    def test_contract_drift_clean(self):
        from suijin.tests.architecture.test_contract_drift import TestContractDrift

        TestContractDrift().test_every_advertised_param_is_accepted()
