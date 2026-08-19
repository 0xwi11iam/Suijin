"""Tests for v3.0.0 safety & resilience features: HITL approvals console,
panic button, DNS-pinned scope enforcement, self-healing dispatch retry,
and the output normalizer."""

import json

import pytest

from suijin.tests.test_cli_commands import run_cli

# ── HITL approvals ─────────────────────────────────────────────────────


class TestApprovals:
    @pytest.fixture(autouse=True)
    def _files(self, tmp_path, monkeypatch):
        from suijin.modules.ops.lib import approvals as ap

        monkeypatch.setattr(ap, "APPROVALS_PATH", tmp_path / "approvals.json")
        monkeypatch.setattr(ap, "SESSION_PATH", tmp_path / "approved_tools.json")
        self.ap = ap
        return ap

    def test_blocked_call_records_pending(self):
        self.ap.record_pending("sqlmap_scan", {"target": "10.0.0.1", "url": "http://x"})
        items = self.ap.list_approvals()
        assert len(items) == 1
        assert items[0]["tool"] == "sqlmap_scan" and items[0]["status"] == "pending"

    def test_duplicate_pending_dedupes(self):
        self.ap.record_pending("sqlmap_scan", {"target": "10.0.0.1"})
        self.ap.record_pending("sqlmap_scan", {"target": "10.0.0.1"})
        assert len(self.ap.list_approvals()) == 1

    def test_args_clipped_for_storage(self):
        self.ap.record_pending("http_request", {"body": "x" * 5000})
        assert len(self.ap.list_approvals()[0]["args"]["body"]) <= 200

    def test_approve_flows_to_session_and_modes(self):
        self.ap.record_pending("msf_run", {})
        self.ap.decide(1, approve=True)
        assert self.ap.decision_for("msf_run") == "approved"
        # the live gate: an approved tool passes HITL
        from suijin.modules.tools.lib.modes import check_mode_restrictions

        assert check_mode_restrictions("msf_run", {}, {"mode_hitl": True}) is None

    def test_deny_blocks_harder_with_message(self):
        self.ap.record_pending("msf_run", {})
        self.ap.decide(1, approve=False)
        assert self.ap.decision_for("msf_run") == "denied"
        from suijin.modules.tools.lib.modes import check_mode_restrictions

        blocked = check_mode_restrictions("msf_run", {}, {"mode_hitl": True})
        assert blocked and "DENIED" in blocked

    def test_latest_decision_wins(self):
        self.ap.record_pending("msf_run", {})
        self.ap.record_pending("msf_run", {"x": 1})  # new pending after re-block
        self.ap.decide(1, approve=False)
        self.ap.decide(2, approve=True)
        assert self.ap.decision_for("msf_run") == "approved"

    def test_clear_resets_verdicts_keeps_log(self):
        self.ap.record_pending("msf_run", {})
        self.ap.decide(1, approve=True)
        self.ap.clear_session()
        assert self.ap.decision_for("msf_run") == "none"
        assert len(self.ap.list_approvals()) == 1  # log preserved

    def test_blocked_hitl_call_creates_real_approval(self, monkeypatch, tmp_path):
        from suijin.modules.ops.lib import approvals as ap

        monkeypatch.setattr(ap, "APPROVALS_PATH", tmp_path / "a.json")
        monkeypatch.setattr(ap, "SESSION_PATH", tmp_path / "s.json")
        from suijin.modules.tools.lib import dispatch

        dispatch.route_tool("msf_run", {"module": "x"}, {"mode_hitl": True})
        items = ap.list_approvals()
        assert any(i["tool"] == "msf_run" for i in items)

    def test_cli_list_approve_deny(self, monkeypatch, tmp_path):
        from suijin.modules.ops.lib import approvals as ap

        monkeypatch.setattr(ap, "APPROVALS_PATH", tmp_path / "a.json")
        monkeypatch.setattr(ap, "SESSION_PATH", tmp_path / "s.json")
        code, out = run_cli(["approvals", "list"])
        assert code == 0 and "No HITL blocks" in out
        ap.record_pending("hydra_brute", {"target": "t"})
        code, out = run_cli(["approvals", "approve", "1"])
        assert "approved" in out and "allowed for this session" in out
        code, out = run_cli(["approvals", "deny", "1"])
        assert "denied" in out
        code, out = run_cli(["approvals", "clear"])
        assert "cleared" in out


# ── Panic button ───────────────────────────────────────────────────────


class TestPanic:
    def test_dry_run_kills_nothing(self, monkeypatch):
        from suijin.modules.ops.lib import panic as pk

        signalled = []
        monkeypatch.setattr(
            pk.subprocess, "run", lambda cmd, **k: signalled.append(cmd) or type("R", (), {"returncode": 0})()
        )
        out = pk.panic(dry_run=True)
        assert "PANIC (dry-run)" in out
        assert signalled  # pkill invoked (it IS the mechanism) but dry-run only reports state files

    def test_clears_live_state(self, tmp_path, monkeypatch):
        import types

        from suijin.modules.ops.lib import panic as pk

        monkeypatch.setattr(pk.subprocess, "run", lambda cmd, **k: type("R", (), {"returncode": 1})())
        state = tmp_path / "blue_kg.json"
        state.write_text("{}")
        fake_tmp = types.SimpleNamespace(gettempdir=lambda: str(tmp_path))
        monkeypatch.setattr(pk, "tempfile", fake_tmp)
        out = pk.panic()
        assert "cleared" in out and not state.exists()

    def test_cli_verb(self, monkeypatch):
        from suijin.modules.ops.lib import panic as pk

        monkeypatch.setattr(pk, "panic", lambda dry_run=False: "PANIC — report")
        code, out = run_cli(["panic"])
        assert code == 0 and "PANIC" in out


# ── DNS-pinned scope enforcement ───────────────────────────────────────


class TestScopeDnsPinning:
    POL = {"allowed_target_scopes": ["lab.internal", "10.0.0.0/8"]}

    def test_ip_scope_unchanged(self):
        from suijin.modules.ops.lib.governance import check_policy

        ok, _ = check_policy("http_request", {"url": "http://10.1.2.3/"}, self.POL)
        assert ok
        ok, why = check_policy("http_request", {"url": "http://203.0.113.1/"}, self.POL)
        assert not ok and "outside allowed scopes" in why

    def test_scoped_host_resolving_in_scope_allowed(self, monkeypatch):
        from suijin.modules.ops.lib import governance as gov

        monkeypatch.setattr(gov, "_resolve_host", lambda h: ["10.0.0.9"])
        ok, _ = gov.check_policy("http_request", {"url": "http://target.lab.internal/"}, self.POL)
        assert ok

    def test_scoped_host_resolving_out_of_scope_blocked(self, monkeypatch):
        from suijin.modules.ops.lib import governance as gov

        monkeypatch.setattr(gov, "_resolve_host", lambda h: ["203.0.113.50"])
        ok, why = gov.check_policy("http_request", {"url": "http://target.lab.internal/"}, self.POL)
        assert not ok and "out-of-scope IP" in why

    def test_unresolvable_host_fails_closed(self, monkeypatch):
        from suijin.modules.ops.lib import governance as gov

        def boom(h):
            raise OSError("no dns")

        monkeypatch.setattr(gov, "_resolve_host", boom)
        ok, why = gov.check_policy("http_request", {"url": "http://target.lab.internal/"}, self.POL)
        assert not ok and "does not resolve" in why

    def test_allow_unresolvable_escape_hatch(self, monkeypatch):
        from suijin.modules.ops.lib import governance as gov

        def boom(h):
            raise OSError("no dns")

        monkeypatch.setattr(gov, "_resolve_host", boom)
        pol = {**self.POL, "allow_unresolvable": True}
        ok, _ = gov.check_policy("http_request", {"url": "http://target.lab.internal/"}, pol)
        assert ok

    def test_dns_cache_memoizes(self, monkeypatch):
        from suijin.modules.ops.lib import governance as gov

        calls = []

        def fake_getaddrinfo(host, _):
            calls.append(host)
            return [(2, 1, 6, "", ("10.0.0.1", 0))]

        import socket as _socket

        monkeypatch.setattr(_socket, "getaddrinfo", fake_getaddrinfo)
        gov._DNS_CACHE.clear()
        gov._resolve_host("x.lab.internal")
        gov._resolve_host("x.lab.internal")
        assert len(calls) == 1  # second lookup served from cache


# ── Self-healing dispatch ──────────────────────────────────────────────


class TestSelfHealing:
    def test_transient_retries_then_succeeds(self, monkeypatch):
        from suijin.modules.tools.lib import dispatch as dp

        monkeypatch.setattr(dp, "_RETRY_BACKOFF_S", (0, 0))
        calls = {"n": 0}

        def flaky(args):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("connection reset by peer")
            return "recovered"

        out = dp._execute_with_healing(flaky, {}, "http_request")
        assert out == "recovered" and calls["n"] == 3

    def test_logical_error_no_retry(self, monkeypatch):
        from suijin.modules.tools.lib import dispatch as dp

        calls = {"n": 0}

        def bad_args(args):
            calls["n"] += 1
            raise ValueError("invalid parameter: url required")

        out = dp._execute_with_healing(bad_args, {}, "http_request")
        assert calls["n"] == 1  # no retry burn on logical errors
        assert "Tool Error" in out and "error" in out

    def test_persistent_transient_reports(self, monkeypatch):
        from suijin.modules.tools.lib import dispatch as dp

        monkeypatch.setattr(dp, "_RETRY_BACKOFF_S", (0, 0))

        def always_down(args):
            raise TimeoutError("request timed out")

        out = dp._execute_with_healing(always_down, {}, "nmap_scan")
        assert "transient failure persisted" in out
        assert "3/3" in out

    def test_route_tool_survives_exceptions(self, monkeypatch):
        from suijin.modules.tools.lib import dispatch as dp

        monkeypatch.setattr(dp, "_build_routes", lambda cfg: {"boom_tool": lambda a: 1 / 0})
        out = dp.route_tool("boom_tool", {}, {})
        assert "Tool Error (boom_tool)" in out and "division by zero" in out


# ── Output normalizer ──────────────────────────────────────────────────


NMAP = """Starting Nmap 7.94
PORT     STATE SERVICE VERSION
22/tcp   open  ssh     OpenSSH 8.9p1
80/tcp   open  http    Apache httpd 2.4.49
443/tcp  open  https    nginx 1.18.0
Nmap done: 1 IP address (3 hosts up)"""

DIRS = """Gobuster v3.6
/admin                (Status: 200) [Size: 1234]
/login                (Status: 200) [Size: 512]
/assets               (Status: 301) [Size: 240]
/.git/HEAD            (Status: 200) [Size: 23]
/nothing              (Status: 404) [Size: 200]
Progress: 100 / 100"""


class TestOutputNormalizer:
    def test_nmap_services_extracted(self):
        from suijin.modules.tools.lib.output_normalizer import parse_nmap

        rows = parse_nmap(NMAP)
        assert [r["port"] for r in rows] == [22, 80, 443]
        assert rows[1]["service"] == "http"
        assert rows[1]["product"] == "Apache httpd"
        assert rows[1]["version"] == "2.4.49"

    def test_dirs_filtered_to_2xx_3xx(self):
        from suijin.modules.tools.lib.output_normalizer import parse_dirs

        rows = parse_dirs(DIRS)
        paths = {r["path"]: r["status"] for r in rows}
        assert paths == {"/admin": 200, "/login": 200, "/assets": 301, "/.git/HEAD": 200}

    def test_auto_detects_nmap_then_dirs(self):
        from suijin.modules.tools.lib.output_normalizer import normalize_output

        assert json.loads(normalize_output(NMAP))[1]["port"] == 80
        assert len(json.loads(normalize_output(DIRS))) == 4

    def test_unknown_output_note(self):
        from suijin.modules.tools.lib.output_normalizer import normalize_output

        assert "No structure recognized" in normalize_output("random text log")

    def test_agent_tool_routed(self):
        from suijin.modules.tools.lib import dispatch

        out = dispatch.route_tool("normalize_output", {"output": NMAP, "kind": "nmap"}, {})
        assert json.loads(out)[0]["service"] == "ssh"


class TestBurpStyleScope:
    """Include/exclude lists + subdomain toggle — the `suijin scope` TUI model."""

    def test_exclude_wins_over_include(self):
        from suijin.modules.ops.lib.governance import check_policy

        pol = {"allowed_target_scopes": ["10.0.0.0/8"], "excluded_scopes": ["10.0.0.66"]}
        ok, why = check_policy("http_request", {"url": "http://10.0.0.66/"}, pol)
        assert not ok and "EXCLUDE" in why
        ok, _ = check_policy("http_request", {"url": "http://10.0.0.5/"}, pol)
        assert ok

    def test_subdomains_toggle_off_blocks_subdomains(self):
        from suijin.modules.ops.lib.governance import check_policy

        # allow_unresolvable: lab.internal has no DNS in tests — the toggle
        # under test is the SUBDOMAIN rule, not DNS pinning
        pol = {"allowed_target_scopes": ["lab.internal"], "allow_subdomains": False, "allow_unresolvable": True}
        ok, _ = check_policy("http_request", {"url": "http://lab.internal/"}, pol)
        assert ok  # exact host passes
        ok, _ = check_policy("http_request", {"url": "http://api.lab.internal/"}, pol)
        assert not ok  # subdomain blocked with toggle OFF

    def test_subdomains_on_matches_subdomains(self):
        from suijin.modules.ops.lib.governance import check_policy

        pol = {"allowed_target_scopes": ["lab.internal"], "allow_subdomains": True, "allow_unresolvable": True}
        ok, _ = check_policy("http_request", {"url": "http://deep.api.lab.internal/"}, pol)
        assert ok

    def test_explicit_wildcard_entry(self):
        from suijin.modules.ops.lib.governance import _ip_in_scope

        assert _ip_in_scope("a.example.com", ["*.example.com"])
        assert _ip_in_scope("example.com", ["*.example.com"])
        assert not _ip_in_scope("example.org", ["*.example.com"])

    def test_default_policy_carries_new_keys(self):
        from suijin.modules.ops.lib.governance import _POLICY_DEFAULT

        assert _POLICY_DEFAULT["allow_subdomains"] is True
        assert _POLICY_DEFAULT["excluded_scopes"] == []
        assert _POLICY_DEFAULT["allow_unresolvable"] is False


class TestNormalizerRichFields:
    def test_nmap_keeps_banner_and_open_filtered(self):
        from suijin.modules.tools.lib.output_normalizer import parse_nmap

        rows = parse_nmap(
            "PORT   STATE SERVICE VERSION\n"
            "22/tcp open  ssh     OpenSSH 8.9p1\n"
            "123/udp open|filtered ntp\n"
            "80/tcp open  http    Apache httpd 2.4.49 ((Unix))\n"
        )
        ports = {r["port"]: r for r in rows}
        assert 123 in ports  # open|filtered is signal too
        assert ports[80]["banner"] == "Apache httpd 2.4.49 ((Unix))"

    def test_dirs_keep_size(self):
        from suijin.modules.tools.lib.output_normalizer import parse_dirs

        rows = parse_dirs("/admin (Status: 200) [Size: 1234]\n/login (Status: 200) [Size: 512]\n")
        by_path = {r["path"]: r for r in rows}
        assert by_path["/admin"]["size"] == 1234
        assert by_path["/login"]["size"] == 512
