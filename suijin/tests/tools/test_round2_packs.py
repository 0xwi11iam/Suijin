"""Round-2 pack tools — offline behavioral tests (no network).

Network tools are tested with mocked responses; pure tools are tested
directly by importing each pack's main.py.
"""

import importlib.util
import json
from pathlib import Path
from unittest import mock

MODULES = Path(__file__).resolve().parents[3] / "suijin" / "modules"


def load_pack(name: str):
    spec = importlib.util.spec_from_file_location(f"suijin_pack_test.{name}", MODULES / name / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeResp:
    def __init__(self, status=200, text="", headers=None, content=b""):
        self.status_code = status
        self.text = text
        self.headers = headers or {}
        self.content = content or text.encode()

    def json(self):
        return json.loads(self.text)


# ── pure tools ─────────────────────────────────────────────────────────


class TestKerbTools:
    def test_hash_format_detection(self):
        m = load_pack("kerbtools")
        out = m.kerb_hash_format("$krb5asrep$23$user@CORP.LOCAL:xyz")
        assert "18200" in out
        out2 = m.kerb_hash_format("$krb5tgs$23$user@CORP:xyz")
        assert "13100" in out2

    def test_spn_candidates(self):
        m = load_pack("kerbtools")
        out = m.spn_candidates("corp.local")
        assert "sql" in out and "corp.local" in out


class TestMacVendor:
    def test_vmware_and_docker(self):
        m = load_pack("macvendor")
        assert "VMware" in m.mac_vendor("00:50:56:ab:cd:ef")
        assert "Docker" in m.mac_vendor("d0:0d:1e:aa:bb:cc")

    def test_invalid_mac(self):
        m = load_pack("macvendor")
        assert m.mac_vendor("not-a-mac").startswith("Error")


class TestUrlTools:
    def test_parse_flags_tricks(self):
        m = load_pack("urltoolz")
        out = m.url_parse("http://admin@0x7f000001/x?a=1")
        assert "0x7f000001" in out
        assert "userinfo @" in out or "hex-encoded" in out

    def test_param_table(self):
        m = load_pack("urltoolz")
        out = m.param_table("http://t/x?a=hello%20world&b=123")
        assert "hello world" in out and "[int]" in out and "[encoded]" in out


class TestSecretScanr:
    def test_scan_finds_known_keys(self):
        m = load_pack("secretscanr")
        out = m.scan_secrets(text="postgres://admin:hunter2@db:5432/x AWS AKIAIOSFODNN7EXAMPLE ghp_" + "A" * 36)
        assert "AWS key" in out or "AKIA" in out
        assert "conn string" in out
        assert "GitHub token" in out

    def test_entropy_ranks_candidates(self):
        m = load_pack("secretscanr")
        out = m.entropy_check(text="token=Z8fKq2mV9xQw4LpR7sTn3BhJ6cDyG1aU5eOiW0zM")
        assert "Z8fKq2" in out


class TestTfSecDockerfile:
    def test_tf_scan_flags_wildcard(self):
        m = load_pack("tfsec")
        out = m.tf_scan('resource "aws_security_group" x {\ningress {\ncidr_blocks = ["0.0.0.0/0"]\n}\n}')
        assert "wildcard" in out

    def test_dockerfile_scan(self):
        m = load_pack("tfsec")
        out = m.dockerfile_scan("FROM ubuntu:latest\nRUN curl x | sh\n")
        assert "latest tag" in out and "curl|bash" in out
        clean = m.dockerfile_scan("FROM ubuntu:22.04\nUSER app\nCOPY . .\n")
        assert "clean" in clean


class TestApkStatics:
    def test_apk_strings_from_zip(self, tmp_path):
        import zipfile

        apk = tmp_path / "t.apk"
        with zipfile.ZipFile(apk, "w") as z:
            z.writestr(
                "classes.dex",
                'const url = "https://api.target.example/v1"; const k = "AIza" + "A" * 35; legacy = "AKIAIOSFODNN7EXAMPLE"',
            )
        m = load_pack("apkstatics")
        out = m.apk_strings(str(apk))
        assert "api.target.example" in out
        assert "SECRETS" in out and "AKIAIOSFODNN7EXAMPLE" in out

    def test_apk_missing(self):
        m = load_pack("apkstatics")
        assert m.apk_strings("/no/such.apk").startswith("Error")


class TestIpaStatics:
    def test_info_plist(self, tmp_path):
        import plistlib
        import zipfile

        ipa = tmp_path / "t.ipa"
        pl = plistlib.dumps(
            {"CFBundleIdentifier": "com.acme.app", "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True}}
        )
        with zipfile.ZipFile(ipa, "w") as z:
            z.writestr("Payload/A.app/Info.plist", pl)
        m = load_pack("ipastatics")
        out = m.ipa_info(str(ipa))
        assert "com.acme.app" in out and "arbitrary loads" in out


# ── network tools (mocked) ────────────────────────────────────────────


class TestGraphql:
    def test_introspection_allowed(self):
        m = load_pack("graphqlprobe")
        schema = json.dumps(
            {
                "data": {
                    "__schema": {
                        "queryType": {"name": "Q"},
                        "mutationType": {"name": "M"},
                        "types": [{"name": "User", "fields": [{"name": "email"}]}],
                    }
                }
            }
        )
        with mock.patch.object(m.requests, "post", return_value=FakeResp(200, schema)):
            out = m.graphql_introspect("http://t/gql")
        assert "INTROSPECTION ALLOWED" in out and "User" in out

    def test_introspection_disabled(self):
        m = load_pack("graphqlprobe")
        body = json.dumps({"errors": [{"message": "introspection disabled"}]})
        with mock.patch.object(m.requests, "post", return_value=FakeResp(200, body)):
            assert "disabled" in m.graphql_introspect("http://t/gql")


class TestOpenapi:
    def test_parse_endpoints(self):
        m = load_pack("openapik")
        spec = json.dumps(
            {
                "info": {"title": "API", "version": "1"},
                "paths": {"/admin/users": {"get": {}, "delete": {}}, "/ping": {"get": {}}},
            }
        )
        out = m.openapi_parse(spec, base_url="https://t")
        assert "/admin/users" in out and "high-value" in out and "https://t/ping" in out


class TestRedirectHunter:
    def test_open_redirect_detected(self):
        m = load_pack("redirecthunter")
        with mock.patch.object(
            m.requests, "get", return_value=FakeResp(302, "", headers={"Location": "https://example.org/safe-canary"})
        ):
            out = m.open_redirect_check("http://t/login?next=/home")
        assert "OPEN REDIRECT" in out


class TestBackupHunter:
    def test_bak_found(self):
        m = load_pack("backuphunter")
        with mock.patch.object(
            m.requests,
            "get",
            side_effect=lambda u, **k: (
                FakeResp(200, "dbpass=x" * 40, {"content-type": "text/plain"})
                if u.endswith(".bak")
                else FakeResp(404, "nope")
            ),
        ):
            out = m.backup_file_probe("http://t", "config.php")
        assert "config.php.bak" in out


class TestVulnGatedPacks:
    def test_binary_tool_reports_missing(self):
        m = load_pack("awsenumer")
        with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            out = m.aws_identity()
            assert "not installed" in out


class TestPortscan:
    def test_tcp_scan_reports_closed(self, monkeypatch):
        m = load_pack("portscanx")
        import socket as s

        class FakeSock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def close(self):
                pass

        def boom(addr, timeout=None, *args, **kw):
            if addr[1] == 80:
                return FakeSock()
            raise s.timeout()

        monkeypatch.setattr(s, "create_connection", boom)
        out = m.tcp_scan("10.10.10.10")
        assert "80" in out  # the open one is listed


class TestEmailHarvest:
    def test_harvest_with_junk_filter(self):
        m = load_pack("emailharvest")
        out = m.harvest_emails(text="contact: bob@acme.com, example@example.com press@acme.com")
        assert "bob@acme.com" in out and "example@example.com" not in out
