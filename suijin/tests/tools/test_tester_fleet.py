"""Wave A-D gym — skill library, selection table, dispatch, capture, tool handoffs.
Full-chain Citadel scenario: select_lanes picks the RIGHT lanes for Citadel's surfaces."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from suijin.modules.tools.lib.tester_fleet import TESTER_DOCTRINES, dispatch_testers, select_lanes  # noqa: E402


class TestSkillLibrary:
    def test_search_finds_sqli(self):
        from suijin.modules.tools.lib.skill_library import skill_search

        out = skill_search("sqli")
        assert "wstg-inpv" in out

    def test_search_finds_jwt(self):
        from suijin.modules.tools.lib.skill_library import skill_search

        out = skill_search("jwt")
        assert "attack-jwt" in out

    def test_load_returns_body(self):
        from suijin.modules.tools.lib.skill_library import skill_load

        out = skill_load("attack-jwt")
        assert "JWT" in out or "jwt" in out
        assert not out.startswith("Error")

    def test_load_bad_id(self):
        from suijin.modules.tools.lib.skill_library import skill_load

        assert skill_load("nonexistent").startswith("Error")


class TestSelectionTable:
    def test_numeric_id_selects_idor(self):
        lanes = select_lanes(url="http://t.com/api/orders/123", method="GET", params=["id=123"])
        names = [lane for lane, _ in lanes]
        assert "idor" in names

    def test_finance_selects_business_logic(self):
        lanes = select_lanes(url="http://t.com/checkout", method="POST",
                             body_fields=["amount", "coupon", "cart_id"])
        names = [lane for lane, _ in lanes]
        assert "business-logic" in names

    def test_url_param_selects_ssrf(self):
        lanes = select_lanes(url="http://t.com/fetch", method="POST",
                             body_fields=["url", "callback"])
        names = [lane for lane, _ in lanes]
        assert "ssrf" in names

    def test_upload_selects_file_attacks(self):
        lanes = select_lanes(url="http://t.com/upload", method="POST",
                             body_fields=["file", "attachment"])
        names = [lane for lane, _ in lanes]
        assert "file-attacks" in names

    def test_free_text_selects_injection(self):
        lanes = select_lanes(url="http://t.com/search", method="GET",
                             params=["query=hello"])
        names = [lane for lane, _ in lanes]
        assert "injection" in names

    def test_injection_floor_on_any_text(self):
        lanes = select_lanes(url="http://t.com/profile", method="POST",
                             body_fields=["name", "bio", "message"])
        names = [lane for lane, _ in lanes]
        assert "injection" in names and "mass-assignment" in names

    def test_admin_path_selects_authz(self):
        lanes = select_lanes(url="http://t.com/admin/users", method="GET", params=[])
        names = [lane for lane, _ in lanes]
        assert "authz" in names

    def test_login_selects_authn(self):
        lanes = select_lanes(url="http://t.com/login", method="POST",
                             body_fields=["username", "password"])
        names = [lane for lane, _ in lanes]
        assert "authn" in names

    def test_max_lanes_capped(self):
        lanes = select_lanes(url="http://t.com/admin/api/orders/123/transfer?callback=x&message=y",
                             method="POST", body_fields=["amount", "coupon", "name", "file"])
        assert len(lanes) <= 4


class TestDispatch:
    def test_dispatch_returns_ready_tasks(self):
        out = dispatch_testers(url="http://t.com/api/orders/123", method="GET", params=["id=123"])
        d = json.loads(out)
        assert d["dispatch"] >= 1
        assert any("test" in t["task"].lower() for t in d["tasks"])
        assert all("coverage_check" in t.get("coverage", t["task"]) for t in d["tasks"])

    def test_dispatch_with_explicit_lanes(self):
        out = dispatch_testers(url="http://t.com/x", lanes=["injection"])
        d = json.loads(out)
        assert d["lanes"] == ["injection"]
        assert "SQLi" in d["tasks"][0]["task"] or "injection" in d["tasks"][0]["task"].lower()

    def test_all_8_doctrines_present(self):
        assert len(TESTER_DOCTRINES) == 8
        for lane, doctrine in TESTER_DOCTRINES.items():
            assert len(doctrine) > 100, f"{lane} doctrine too short"
            assert "coverage" in doctrine.lower() or "record" in doctrine.lower(), f"{lane} must reference evidence"


class TestToolSurface:
    def test_all_routed(self):
        from suijin.modules.tools.lib.dispatch import route_tool

        for tool in ("skill_search", "skill_load", "dispatch_testers", "select_lanes"):
            out = route_tool(tool, {}, {})
            assert "TOOL NOT FOUND" not in str(out), tool
