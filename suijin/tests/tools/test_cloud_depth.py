"""Cloud depth upgrades — real enumeration (mocked CLI fixtures)."""

import importlib.util
from unittest import mock


def load_pack(name):
    spec = importlib.util.spec_from_file_location(f"depth.{name}", f"suijin/modules/{name}/main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestAwsEnum:
    def setup_method(self):
        self.mod = load_pack("awsenumer")

    def _fake_aws(self, service, *args):
        fixtures = {
            "sts": ({"Account": "123456789012", "Arn": "arn:aws:iam::123:user/test", "UserId": "AIDA123"}, ""),
            "s3api": (["bucket-a", "bucket-b"], "") if "list-buckets" in args else ({"Grants": []}, ""),
            "iam": (["role-1"], "") if "list-roles" in args else (["user-1"], ""),
            "ec2": ([{"ID": "i-123", "Type": "t3.micro", "State": "running", "IP": "10.0.0.1"}], ""),
            "secretsmanager": (["secret-1"], ""),
            "lambda": ([{"Name": "fn-1", "Runtime": "python3.12", "Role": "role"}], ""),
        }
        return fixtures.get(service, ({}, ""))

    def test_full_enumeration_all_sections(self):
        with mock.patch.object(self.mod, "_aws", side_effect=self._fake_aws):
            out = self.mod.aws_enum("true")
            for expected in ["123456789012", "bucket-a", "role-1", "user-1", "i-123", "secret-1", "fn-1"]:
                assert expected in out, f"missing {expected}"

    def test_quick_mode_skips_iam_ec2(self):
        with mock.patch.object(self.mod, "_aws", side_effect=self._fake_aws):
            out = self.mod.aws_enum("false")
            assert "bucket-a" in out
            assert "role-1" not in out  # quick mode skips IAM

    def test_cli_not_installed(self):
        with mock.patch.object(self.mod, "_aws", return_value=(None, "Error: aws CLI not installed")):
            out = self.mod.aws_identity()
            assert "not installed" in out

    def test_public_read_flagged(self):
        acl = {
            "Grants": [{"Grantee": {"URI": "http://acs.amazonaws.com/groups/global/AllUsers"}, "Permission": "READ"}]
        }

        def fake(service, *args):
            if "list-buckets" in args:
                return ["exposed-bucket"], ""
            if "get-bucket-acl" in args:
                return acl, ""
            return {}, ""

        with mock.patch.object(self.mod, "_aws", side_effect=fake):
            out = self.mod.aws_s3()
            assert "[PUBLIC-READ]" in out

    def test_no_unreachable_branch(self):
        """The old args_sig NameError branch is gone."""
        import inspect

        src = inspect.getsource(self.mod)
        assert "args_sig" not in src


class TestAdEnumDepth:
    """v5.2: ad_null_session was a port-knock; now 6 real functions."""

    def setup_method(self):
        self.mod = load_pack("adenum")

    def test_asrep_roast_parses_hashes(self):
        fake_out = "$krb5asrep$23$user@DOMAIN:hash123\n$krb5asrep$23$admin@DOMAIN:hash456"
        with mock.patch.object(self.mod, "_run", return_value=(fake_out, "", 0)):
            out = self.mod.ad_asrep_roast("test.corp")
            assert "2" in out and "$krb5asrep$" in out and "hashcat -m 18200" in out

    def test_asrep_roast_impacket_missing(self):
        with mock.patch.object(self.mod, "_run", return_value=("", "not installed: GetNPUsers.py", -1)):
            out = self.mod.ad_asrep_roast("test.corp")
            assert "impacket not installed" in out

    def test_kerberoast_parses_tickets(self):
        fake_out = "ServicePrincipalName:\n  MSSQLSvc/sql01.test.corp\n$krb5tgs$23$sql:$hash"
        with mock.patch.object(self.mod, "_run", return_value=(fake_out, "", 0)):
            out = self.mod.ad_kerberoast("test.corp", "TEST\\user", "pass")
            assert "$krb5tgs$" in out and "hashcat -m 13100" in out

    def test_smb_shares_fallback(self):
        # impacket fails, smbclient succeeds
        calls = []

        def fake_run(argv, timeout=60):
            calls.append(argv[0])
            if argv[0] == "impacket-smbclient":
                return "", "failed", 1
            return "ADMIN$ Disk\nC$ Disk\nIPC$ IPC", "", 0

        with mock.patch.object(self.mod, "_run", side_effect=fake_run):
            out = self.mod.ad_smb_shares("10.0.0.1")
            assert "ADMIN$" in out and "IPC$" in out

    def test_ldap_search_counts_entries(self):
        fake = "dn: CN=alice,DC=test,DC=corp\ndn: CN=bob,DC=test,DC=corp"
        with mock.patch.object(self.mod, "_run", return_value=(fake, "", 0)):
            out = self.mod.ad_ldap_search("test.corp")
            assert "2 entries" in out and "CN=alice" in out

    def test_null_session_clean(self):
        """The old vestigial '388 if False else 389' is gone."""
        import inspect

        src = inspect.getsource(self.mod)
        assert "if False" not in src


class TestReconChainDepth:
    """v5.2: recon_chain adds whatweb fingerprinting on HTTP services."""

    def test_web_fingerprint_called_for_http(self):
        """whatweb runs on the first HTTP port and its output surfaces."""
        import suijin.modules.loader as loader_mod
        from suijin.modules.tools.lib import recon

        services = [{"port": 80, "proto": "tcp", "service": "http", "banner": "nginx 1.24"}]
        fake_whatweb = mock.MagicMock(return_value="nginx, PHP, jQuery")

        with mock.patch.object(loader_mod, "get_module_tools", return_value={"whatweb_scan": fake_whatweb}):
            result = recon._fingerprint_web("target.example", services)

        fake_whatweb.assert_called_once()
        assert result == "nginx, PHP, jQuery"

    def test_web_fingerprint_skips_non_http(self):
        import suijin.modules.loader as loader_mod
        from suijin.modules.tools.lib import recon

        services = [{"port": 22, "proto": "tcp", "service": "ssh", "banner": "OpenSSH 9"}]
        fake_whatweb = mock.MagicMock()

        with mock.patch.object(loader_mod, "get_module_tools", return_value={"whatweb_scan": fake_whatweb}):
            result = recon._fingerprint_web("target.example", services)

        fake_whatweb.assert_not_called()
        assert result == ""

    def test_chain_includes_fingerprint_section(self):
        """The chain output has a Web fingerprint section when whatweb returns data."""
        import inspect

        from suijin.modules.tools.lib import recon

        src = inspect.getsource(recon.recon_chain)
        assert "Web fingerprint" in src
        assert "_fingerprint_web" in src
