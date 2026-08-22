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
