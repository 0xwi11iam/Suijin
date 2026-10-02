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
        assert "middle truncated" in out  # head+tail digest, bounded

    def test_unknown_tool_defaults_auto(self):
        assert get_offload_mode("totally_unknown_tool") == "auto"

    def test_digest_is_head_and_tail_and_bounded(self):
        body = "HEAD!" + "B" * (oo.OFFLOAD_THRESHOLD + 600) + "!TAIL"
        out, offloaded = oo.maybe_offload("nmap_scan", body)
        preview = out.split("Preview:\n", 1)[1]
        assert preview.startswith("HEAD!") and preview.endswith("!TAIL")  # both ends survive
        assert len(preview) < 1_700  # the middle is dropped, digest bounded


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
