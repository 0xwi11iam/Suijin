"""Tests for compliance mapping — pure lookup + engagement loading."""

import json

import pytest

from suijin.modules.ops.lib import compliance as comp


class TestClassify:
    @pytest.mark.parametrize(
        "ftype,desc,expected_cwe",
        [
            ("sqli", "union select in login", "CWE-89"),
            ("xss", "reflected script tag", "CWE-79"),
            ("ssti", "template injection via data param", "CWE-1336"),
            ("xxe", "external entity in export", "CWE-611"),
            ("ssrf", "webhook fetches metadata", "CWE-918"),
            ("idor", "cross-tenant document read", "CWE-639"),
            ("auth_bypass", "X-Admin header trusted", "CWE-287"),
            ("path_traversal", "../../etc/passwd download", "CWE-22"),
            ("cmd", "command injection via ping", "CWE-78"),
            ("info_disclosure", "debug endpoint leaks env", "CWE-200"),
            ("privesc", "kernel exploit", "CWE-269"),
            ("behavior", "WAF blocks OR 1=1", "CWE-693"),  # waf keyword
        ],
    )
    def test_known_classes(self, ftype, desc, expected_cwe):
        cwe, _owasp, _attack = comp.classify_finding(ftype, desc)
        assert cwe == expected_cwe

    def test_specific_beats_generic(self):
        # 'blind sqli' must hit the SQLi row, not a generic fallback
        cwe, _, _ = comp.classify_finding("finding", "blind sqli in search field")
        assert cwe == "CWE-89"

    def test_unknown_falls_back(self):
        cwe, owasp, attack = comp.classify_finding("totally novel", "weird thing")
        assert (cwe, owasp, attack) == comp._FALLBACK

    def test_all_mappings_wellformed(self):
        for keyword, cwe, owasp, attack in comp.MAPPING:
            assert re.match(r"^CWE-\d+$", cwe), cwe
            assert owasp.startswith("A") and len(owasp) > 5, owasp
            assert re.match(r"^T\d{4}$", attack), attack
            assert keyword.strip()


import re  # noqa: E402 — used by the parametrized wellformed test


class TestMapSummarize:
    def test_annotate_and_summarize(self):
        mapped = comp.map_findings(
            [
                {"type": "sqli", "description": "login bypass", "severity": "high"},
                {"type": "xss", "description": "stored", "severity": "medium"},
                {"type": "sqli", "description": "search", "severity": "high"},
            ]
        )
        assert all("cwe" in m and "owasp" in m and "attack" in m for m in mapped)
        s = comp.summarize(mapped)
        assert s["cwe"]["CWE-89"] == 2
        assert s["cwe"]["CWE-79"] == 1

    def test_empty(self):
        assert comp.map_findings([]) == []
        assert comp.summarize([]) == {"cwe": {}, "owasp": {}, "attack": {}}


class TestLoadRender:
    def test_load_newest_by_default(self, tmp_path):
        import os

        for name, old in (("older", 1000), ("newer", 2000)):
            f = tmp_path / f"{name}.json"
            f.write_text(json.dumps({"findings": [{"type": "sqli", "description": name}]}))
            os.utime(f, (old, old))
        findings = comp.load_findings(workspace=tmp_path)
        # no engagement name -> aggregates every trail
        assert len(findings) == 2

    def test_named_engagement_filters(self, tmp_path):
        (tmp_path / "alpha_eng.json").write_text(json.dumps({"findings": [{"type": "sqli"}]}))
        (tmp_path / "beta_eng.json").write_text(json.dumps({"findings": [{"type": "xss"}]}))
        findings = comp.load_findings("alpha", workspace=tmp_path)
        assert len(findings) == 1 and findings[0]["type"] == "sqli"

    def test_render_table(self):
        out = comp.render(
            comp.map_findings(
                [
                    {"type": "sqli", "description": "login", "severity": "high"},
                    {"type": "ssrf", "description": "webhook", "severity": "critical"},
                ]
            )
        )
        assert "CWE-89" in out and "CWE-918" in out
        assert "A03 Injection" in out
        assert "By OWASP Top-10 2021" in out and "By ATT&CK technique" in out

    def test_render_empty(self):
        assert "No findings" in comp.render([])


class TestCliVerb:
    def test_compliance_cli(self, monkeypatch, tmp_path):
        from suijin.tests.console.test_cli_commands import run_cli

        monkeypatch.setattr("suijin.modules.platform.lib.workspace.WORKSPACE_DIR", tmp_path)
        code, out = run_cli(["compliance"])
        assert code == 0 and "No findings" in out

        (tmp_path / "outputs" / "audit_trails").mkdir(parents=True, exist_ok=True)
        (tmp_path / "outputs" / "audit_trails" / "eng.json").write_text(
            json.dumps({"findings": [{"type": "sqli", "description": "login bypass", "severity": "high"}]})
        )
        code, out = run_cli(["compliance"])
        assert code == 0 and "CWE-89" in out and "A03 Injection" in out
