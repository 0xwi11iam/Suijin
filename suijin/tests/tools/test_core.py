"""Tests for core graph, dispatch, and think_node."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestDispatchGuardrails:
    """Verify command safety guardrails actually work."""

    def test_dangerous_rmrf_blocked(self):
        from suijin.modules.tools.lib.dispatch import is_dangerous

        dangerous, pattern = is_dangerous("rm -rf /")
        assert dangerous
        assert pattern

    def test_dangerous_mkfs_blocked(self):
        from suijin.modules.tools.lib.dispatch import is_dangerous

        dangerous, _ = is_dangerous("mkfs.ext4 /dev/sda")
        assert dangerous

    def test_dangerous_fork_bomb_blocked(self):
        from suijin.modules.tools.lib.dispatch import is_dangerous

        dangerous, _ = is_dangerous(":(){ :|:& };:")
        assert dangerous

    def test_dangerous_chmod_blocked(self):
        from suijin.modules.tools.lib.dispatch import is_dangerous

        dangerous, _ = is_dangerous("chmod 777 /")
        assert dangerous

    def test_safe_command_passes(self):
        from suijin.modules.tools.lib.dispatch import is_dangerous

        dangerous, _ = is_dangerous("nmap -sV 127.0.0.1")
        assert not dangerous

    def test_safe_python_passes(self):
        from suijin.modules.tools.lib.dispatch import is_dangerous

        dangerous, _ = is_dangerous("python3 -c 'print(1+1)'")
        assert not dangerous

    def testconfirm_global_action_blocks_by_default(self):
        # Should block unless SUIJIN_AUTO_APPROVE=true
        import os

        from suijin.modules.tools.lib.dispatch import confirm_global_action

        old = os.environ.pop("SUIJIN_AUTO_APPROVE", None)
        result = confirm_global_action("rm -rf /", "rm -rf /")
        if old:
            os.environ["SUIJIN_AUTO_APPROVE"] = old
        assert result is False

    def test_workspace_path_rejects_absolute(self):
        from suijin.modules.tools.lib.dispatch import resolve_workspace_path

        with pytest.raises(PermissionError):
            resolve_workspace_path("/etc/passwd")

    def test_workspace_path_allows_tmp(self):
        from suijin.modules.tools.lib.dispatch import resolve_workspace_path

        result = resolve_workspace_path("/tmp/test.txt")
        assert "/tmp" in str(result)


class TestSecretPatterns:
    """Verify credential detection patterns."""

    def test_aws_key_detected(self):
        import re

        from suijin.modules.platform.lib.security.secret_patterns import SECRET_PATTERNS

        # The pattern matches "AWS_ACCESS_KEY_ID=AKIA..." format or bare keys in context
        found = False
        for name, pattern in SECRET_PATTERNS.items():
            if "aws" in name.lower():
                if re.search(pattern, "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"):
                    found = True
                    break
                if re.search(pattern, "AKIAIOSFODNN7EXAMPLE"):
                    found = True
                    break
        assert found, "no AWS pattern matched the example access key"
        assert any("aws" in name.lower() for name in SECRET_PATTERNS)

    def test_jwt_detected(self):
        import re

        from suijin.modules.platform.lib.security.secret_patterns import SECRET_PATTERNS

        found = False
        for name, pattern in SECRET_PATTERNS.items():
            if "jwt" in name.lower() and re.search(pattern, "JWT_SECRET=super-secret-key-change-me-2024"):
                found = True
                break
        assert found, "no JWT pattern matched the example secret"
        assert any("jwt" in name.lower() for name in SECRET_PATTERNS)


class TestErrorTypes:
    """Verify structured error types work."""

    def test_blue_error_creation(self):
        from suijin.modules.blueteam.lib.blue.errors import BlueError, ErrorSeverity

        e = BlueError("test error", severity=ErrorSeverity.WARNING, source="test")
        d = e.to_dict()
        assert d["error"] == "test error"
        assert d["severity"] == "warning"
        assert d["recoverable"] is True

    def test_firewall_error(self):
        from suijin.modules.blueteam.lib.blue.errors import ErrorSeverity, FirewallError

        e = FirewallError("block failed", severity=ErrorSeverity.CRITICAL)
        assert e.source == "firewall"
        assert e.severity == ErrorSeverity.CRITICAL

    def test_ok_result(self):
        from suijin.modules.blueteam.lib.blue.errors import ok

        r = ok("done")
        assert r["status"] == "ok"
        assert r["result"] == "done"

    def test_err_result(self):
        from suijin.modules.blueteam.lib.blue.errors import BlueError, ErrorSeverity, err

        e = BlueError("failed", severity=ErrorSeverity.ERROR)
        r = err(e)
        assert r["status"] == "error"
        assert r["error"]["error"] == "failed"


class TestConfigValidation:
    """Verify Pydantic config models catch bad configs."""

    def test_blue_config_defaults(self):
        from suijin.modules.platform.lib.config_models import BlueConfig

        c = BlueConfig()
        assert c.scorer.critical_threshold == 8
        assert c.deception.tarpit_delay_seconds == 8.0
        assert c.cost.daily_budget_usd == 5.0

    def test_red_config_defaults(self):
        from suijin.modules.platform.lib.config_models import RedConfig

        c = RedConfig()
        assert c.cost_hard_cap_usd == 0.0  # operator: 0 = unlimited
        assert c.max_iterations == 100000  # operator: effectively infinite

    def test_red_config_rejects_negative_cost(self):
        from pydantic import ValidationError

        from suijin.modules.platform.lib.config_models import RedConfig

        with pytest.raises(ValidationError):  # Pydantic validation error
            RedConfig(cost_hard_cap_usd=-1.0)
