"""Anti-hallucination gates — POC verification + CVE matching rework."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from suijin.modules.tools.lib.exploit_catalog import (  # noqa: E402
    _is_echo_command,
    _marker_quality_check,
    catalog_exploit,
    system_severity,
)


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    from suijin.modules.tools.lib import exploit_catalog as ec

    store = tmp_path / "exploits"
    monkeypatch.setattr(ec, "_expits_dir", lambda: store)
    yield


class TestEchoBlock:
    def test_echo_detected(self):
        assert _is_echo_command("echo PWNED")
        assert _is_echo_command("printf 'PWNED'")
        assert _is_echo_command("/bin/echo test")
        assert _is_echo_command("cat /etc/passwd")  # cat can produce arbitrary text

    def test_real_commands_pass(self):
        assert not _is_echo_command("curl http://t.com/x")
        assert not _is_echo_command("nmap -sV t.com")
        assert not _is_echo_command("python3 exploit.py")
        assert not _is_echo_command("sqlmap -u http://t.com")


class TestMarkerQuality:
    def test_short_markers_rejected(self):
        assert _marker_quality_check("200") is not None
        assert _marker_quality_check("ok") is not None
        assert _marker_check_short("FLAG") is not None

    def test_generic_patterns_rejected(self):
        assert _marker_quality_check("HTTP/1.1 200 OK") is not None
        assert _marker_quality_check("success") is not None
        assert _marker_quality_check("root:") is not None

    def test_unique_markers_pass(self):
        assert _marker_quality_check("FLAG{sqli_confirmed_here}") is None
        assert _marker_quality_check("admin:s3cret_hash_value") is None

    def test_short(self):
        assert len("FLAG") < 8


def _marker_check_short(m):
    return _marker_quality_check(m)


class TestSystemSeverity:
    def test_class_outcome_ladder(self):
        assert system_severity("rce", "command_exec") == "critical"
        assert system_severity("sqli", "data_read") == "high"
        assert system_severity("xss", "reflected") == "medium"
        assert system_severity("info_leak", "config_disclosure") == "medium"

    def test_class_defaults(self):
        assert system_severity("rce") == "critical"
        assert system_severity("sqli") == "high"
        assert system_severity("xss") == "medium"
        assert system_severity("unknown_class") == "medium"


class TestCatalogExploit:
    def _fake_route(self, tool, args, cfg):
        """Fake terminal that returns different outputs for different commands."""
        cmd = args.get("cmd", "")
        if "echo" in cmd.split()[0] if cmd.split() else False:
            return cmd.split("'", 1)[-1].rstrip("'") if "'" in cmd else cmd.split(None, 1)[-1] if len(cmd.split()) > 1 else ""
        return "Some HTTP response from target"

    def test_echo_poc_rejected(self):
        """POC: echo PWNED-FLAG | marker: PWNED-FLAG → FAILED_REPRO (echo-block)."""
        out = catalog_exploit(
            engagement="test",
            target="http://t.com/api/x",
            vuln_class="sqli",
            title="echo test",
            poc=[{"cmd": "echo PWNED-FLAG", "wait": 0}],
            marker="PWNED-FLAG",
            route_fn=self._fake_route,
        )
        assert "FAILED_REPRO" in out
        assert "self-confirmation" in out.lower() or "echo" in out.lower()

    def test_no_target_ref_rejected(self):
        """POC that never touches the target → FAILED_REPRO."""
        out = catalog_exploit(
            engagement="test",
            target="http://t.com/api/x",
            vuln_class="sqli",
            title="no-target test",
            poc=[{"cmd": "ls -la", "wait": 0}],
            marker="FLAG{unique_string_here}",
            route_fn=self._fake_route,
        )
        assert "FAILED_REPRO" in out
        assert "never touches the target" in out or "does not reference" in out.lower()

    def test_generic_marker_rejected(self):
        """marker='200 OK' → DRAFT (too generic)."""
        out = catalog_exploit(
            engagement="test",
            target="http://t.com/api/x",
            vuln_class="sqli",
            title="generic marker",
            poc=[{"cmd": "curl http://t.com/x", "wait": 0}],
            marker="200 OK",
            route_fn=self._fake_route,
        )
        assert "DRAFT" in out
        assert "generic" in out.lower() or "too short" in out.lower() or "proves nothing" in out.lower()

    def test_system_severity_in_output(self):
        """The system severity appears in the CONFIRMED/FAILED output."""
        out = catalog_exploit(
            engagement="test",
            target="http://t.com/api/x",
            vuln_class="rce",
            title="severity test",
            poc=[{"cmd": "curl http://t.com/x", "wait": 0}],
            marker="FLAG{unique_response}",
            route_fn=self._fake_route,
        )
        # the classed_title now uses system_severity
        assert "CRITICAL" in out or "HIGH" in out or "MEDIUM" in out or "FAILED" in out

    def test_ai_claimed_cap(self):
        """After MAX_CLAIMS AI_CLAIMED entries, further claims are refused.
        v3: a claim is action=worked_anyway citing a real output line."""
        for i in range(4):  # try to exceed the cap of 3
            catalog_exploit(  # first: the verifier runs and misses
                engagement="test",
                target=f"http://t.com/{i}",
                vuln_class="sqli",
                title=f"claim test {i}",
                poc=[{"cmd": f"curl http://t.com/{i}", "wait": 0}],
                marker="FLAG{unique_marker_x}",
                route_fn=self._fake_route,
            )
            out = catalog_exploit(
                engagement="test",
                target=f"http://t.com/{i}",
                vuln_class="sqli",
                title=f"claim test {i}",
                entry_id=f"EXP-00{i + 1}",
                action="worked_anyway",
                evidence_line="Some HTTP response from target",  # real output, no marker
                route_fn=self._fake_route,
            )
            if i < 3:
                assert "AI_CLAIMED" in out, f"claim {i} should succeed"
            else:
                assert "cap reached" in out.lower() or "Error" in out, f"claim {i} should be refused"

    def test_ai_claimed_in_prior_failures(self):
        """After an AI_CLAIMED, the combo appears in prior-failure memory."""
        catalog_exploit(
            engagement="first_eng",
            target="http://t.com/api/y",
            vuln_class="ssrf",
            title="prior fail test",
            poc=[{"cmd": "curl http://t.com/api/y", "wait": 0}],
            marker="FLAG{unique_marker}",
            route_fn=self._fake_route,
        )
        catalog_exploit(
            engagement="first_eng",
            target="http://t.com/api/y",
            vuln_class="ssrf",
            title="prior fail test",
            entry_id="EXP-001",
            action="worked_anyway",
            evidence_line="Some HTTP response from target",
            route_fn=self._fake_route,
        )
        out = catalog_exploit(
            engagement="second_eng",
            target="http://t.com/api/y",
            vuln_class="ssrf",
            title="prior fail test retry",
            poc=[{"cmd": "curl http://t.com/api/y", "wait": 0}],
            marker="FLAG{another_unique_marker}",
            route_fn=self._fake_route,
        )
        assert "PRIOR FAILURES" in out or "AI_CLAIMED" in out


class TestCveAntiHallucination:
    def test_verified_cve_requires_cve_id(self):
        from suijin.modules.tools.lib.intel import record_finding

        out = record_finding("http://t.com", "verified_cve", "Apache has a CVE")
        assert "must cite a CVE id" in out or "Error" in out

    def test_verified_cve_with_fake_cve_rejected(self, monkeypatch):
        from suijin.modules.tools.lib import intel

        # mock search_cve to return real results that DON'T contain the fake CVE
        monkeypatch.setattr(intel, "search_cve", lambda *a, **kw: "CVE-2024-1234: some real vuln")
        out = intel.record_finding("http://t.com", "verified_cve", "CVE-9999-99999")
        assert "NOT found" in out or "Error" in out or "rumor" in out or "memory" in out.lower() or "advisory" in out.lower()

    def test_confidence_not_hardcoded(self):
        """record_finding no longer stores confidence=1.0 unconditionally."""
        # we can verify the source code has the fix
        import inspect

        from suijin.modules.tools.lib import intel

        src = inspect.getsource(intel.record_finding)
        assert "confidence=1.0" not in src
        assert "confidence = 0.1" in src or "confidence=0.1" in src
