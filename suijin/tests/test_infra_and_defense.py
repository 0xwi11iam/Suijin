"""Tests for live low-coverage infrastructure: output offloading, firewall
defense, hotfix patch generators, traffic-log tailing, and Metasploit
availability probing (all subprocesses mocked — nothing leaves the box).
"""

import pytest

from suijin.modules.platform.lib.infra import output_offload as oo
from suijin.modules.platform.lib.infra.tool_offload_policy import get_offload_mode


class TestOutputOffload:
    @pytest.fixture(autouse=True)
    def _ws(self, tmp_path, monkeypatch):
        monkeypatch.setattr(oo, "WORKSPACE_DIR", tmp_path)

    def test_never_policy_passthrough(self):
        out, offloaded = oo.maybe_offload("search_kb", "x" * 200_000)
        assert offloaded is False
        assert out == "x" * 200_000  # never-offload tools keep full inline

    def test_auto_below_threshold_inline(self):
        out, offloaded = oo.maybe_offload("nmap_scan", "short output")
        assert offloaded is False and out == "short output"

    def test_auto_above_threshold_offloads(self, tmp_path):
        big = "A" * (oo.OFFLOAD_THRESHOLD + 1)
        out, offloaded = oo.maybe_offload("nmap_scan", big)
        assert offloaded is True
        assert "[OUTPUT OFFLOADED" in out
        assert str(tmp_path / "outputs") in out
        files = list((tmp_path / "outputs").glob("nmap_scan_*.txt"))
        assert len(files) == 1 and files[0].read_text() == big
        assert out.endswith("…")  # preview truncated with ellipsis

    def test_unknown_tool_defaults_auto(self):
        assert get_offload_mode("totally_unknown_tool") == "auto"

    def test_preview_boundary_exact_500(self):
        out, offloaded = oo.maybe_offload("nmap_scan", "B" * (oo.OFFLOAD_THRESHOLD + 600))
        preview = out.split("Preview:\n", 1)[1]
        assert len(preview) == 501  # 500 chars + ellipsis


class TestFirewall:
    def test_validate_ip_accepts_valid(self):
        from suijin.core.blue.defense import firewall as fw

        assert fw._validate_ip(" 10.0.0.7 ") == "10.0.0.7"
        assert fw._validate_ip("::1") == "::1"

    def test_validate_ip_rejects_garbage(self):
        from suijin.core.blue.defense import firewall as fw

        with pytest.raises(ValueError, match="Invalid IP"):
            fw._validate_ip("not-an-ip; rm -rf")

    def test_block_ip_invalid_returns_error(self, monkeypatch):
        from suijin.core.blue.defense import firewall as fw

        calls = []
        monkeypatch.setattr(fw.subprocess, "run", lambda *a, **k: calls.append(a) or None)
        out = fw.block_ip("999.999.999.999")
        assert out.startswith("Invalid IP")
        assert calls == []  # validation happens BEFORE any subprocess call

    def test_block_ip_success_and_failure(self, monkeypatch):
        from suijin.core.blue.defense import firewall as fw

        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if "10.0.0.9" in cmd:

                class R:
                    returncode = 0

                return R()
            raise OSError("no iptables")

        monkeypatch.setattr(fw.subprocess, "run", fake_run)
        assert fw.block_ip("10.0.0.9") == "Blocked 10.0.0.9"
        assert "DROP" in calls[0] and "10.0.0.9" in calls[0]
        out = fw.block_ip("10.0.0.8")  # OSError path
        assert out.startswith("Failed to block")

    def test_unblock_uses_delete_rule(self, monkeypatch):
        from suijin.core.blue.defense import firewall as fw

        calls = []
        monkeypatch.setattr(fw.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
        fw.unblock_ip("172.16.0.4")
        # ["sudo", "iptables", "-D", "INPUT", ...] — index 2 is the rule op
        assert calls[0][2] == "-D" and "172.16.0.4" in calls[0]

    def test_list_blocks_filters_drop_lines(self, monkeypatch):
        from suijin.core.blue.defense import firewall as fw

        class R:
            stdout = "Chain INPUT\nDROP       all -- 10.0.0.1\nACCEPT     all -- 0.0.0.0\nDROP       all -- 10.0.0.2\n"

        monkeypatch.setattr(fw.subprocess, "run", lambda *a, **k: R())
        lines = fw.list_blocks()
        assert len(lines) == 2 and all("DROP" in ln for ln in lines)


class TestTailFile:
    def test_yields_appended_lines(self, tmp_path):
        from suijin.modules.ops.lib.housekeeping import tail_file

        log = tmp_path / "log.jsonl"
        log.write_text("one\ntwo\n")
        gen = tail_file(log, poll=0.01)
        assert [next(gen), next(gen)] == ["one", "two"]

    def test_truncation_resets_position(self, tmp_path):
        from suijin.modules.ops.lib.housekeeping import tail_file

        log = tmp_path / "log.jsonl"
        log.write_text("aa\nbb\n")
        gen = tail_file(log, poll=0.01)
        assert [next(gen), next(gen)] == ["aa", "bb"]  # drain the buffered read
        log.write_text("cc\n")  # file shrunk (rotation)
        assert next(gen) == "cc"

    def test_missing_file_waits_then_reads(self, tmp_path):
        from suijin.modules.ops.lib.housekeeping import tail_file

        log = tmp_path / "late.jsonl"
        gen = tail_file(log, poll=0.01)
        log.write_text("arrived\n")
        assert next(gen) == "arrived"


class TestMsfCheck:
    def test_unavailable_without_rpc_or_console(self, monkeypatch):
        from suijin.modules.tools.lib import metasploit as msf

        monkeypatch.setattr(msf, "_msf_rpc_connect", lambda cfg: (None, None))

        class R:
            stdout = ""

        monkeypatch.setattr(msf.subprocess, "run", lambda *a, **k: R())
        out = msf.msf_check({})
        assert "NOT detected" in out

    def test_console_fallback_detected(self, monkeypatch):
        from suijin.modules.tools.lib import metasploit as msf

        monkeypatch.setattr(msf, "_msf_rpc_connect", lambda cfg: (None, None))

        class R:
            stdout = "/usr/bin/msfconsole\n"

        monkeypatch.setattr(msf.subprocess, "run", lambda *a, **k: R())
        out = msf.msf_check({})
        assert "msfconsole" in out and "No RPC daemon" in out

    def test_rpc_connected_reports_version(self, monkeypatch):
        from suijin.modules.tools.lib import metasploit as msf

        class Proxy:
            class core:
                @staticmethod
                def version(token):
                    return "6.3.0"

        monkeypatch.setattr(msf, "_msf_rpc_connect", lambda cfg: (Proxy(), "tok"))
        out = msf.msf_check({})
        assert "6.3.0" in out and "RPC connected" in out
