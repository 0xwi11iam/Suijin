"""Tests for medusa/tools/recon.py — recon chaining and version-to-CVE."""
import json

from medusa.tools.recon import parse_services, recon_chain, version_to_cves

NMAP_OUT = """[COMMAND] nmap -sV -sC -T4 127.0.0.1
[EXIT] 0 (2.0s)
[STDOUT]
PORT     STATE SERVICE VERSION
22/tcp   open  ssh     OpenSSH 8.2p1 Ubuntu 4ubuntu0.5
80/tcp   open  http    Apache httpd 2.4.49
443/tcp  open  https
"""


class TestParseServices:
    def test_parses_open_services(self):
        services = parse_services(NMAP_OUT)
        ports = {s["port"]: s for s in services}
        assert 22 in ports and 80 in ports and 443 in ports
        assert ports[80]["service"] == "http"
        assert "2.4.49" in ports[80]["banner"]

    def test_empty_output(self):
        assert parse_services("") == []


class TestVersionToCves:
    def test_extracts_versions_and_calls_cve(self, monkeypatch):
        called = []

        def fake_search_cve(software, config, version=None, limit=5):
            called.append((software, version))
            return f"CVE for {software} {version}"

        monkeypatch.setattr("medusa.tools.intel.search_cve", fake_search_cve)
        services = [{"port": 80, "proto": "tcp", "service": "http", "banner": "Apache httpd 2.4.49"}]
        results = version_to_cves(services, {})
        assert results[0][1] == "httpd"
        assert results[0][2] == "2.4.49"
        assert called == [("httpd", "2.4.49")]


class TestReconChain:
    def test_recon_chain_assembles_report(self, monkeypatch):
        def fake_nmap(target, flags="-sV -sC"):
            return NMAP_OUT

        monkeypatch.setattr("medusa.modules.loader.get_module_tools",
                            lambda: {"nmap_scan": fake_nmap})

        def fake_search_cve(software, config, version=None, limit=5):
            return f"CVE for {software} {version}"

        monkeypatch.setattr("medusa.tools.intel.search_cve", fake_search_cve)

        report = recon_chain("127.0.0.1", config={})
        assert "# Recon chain: 127.0.0.1" in report
        assert "Services discovered" in report
        assert "CVE matches" in report
        assert "CVE for httpd 2.4.49" in report
