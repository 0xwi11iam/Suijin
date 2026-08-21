"""Stealth — less noisy, zero performance loss.

Real tests: sticky identity consistency, full browser header shape,
burst-limiter math (manual probes pay 0s, bursts get spaced), command
sanitizer (caps added, idempotent, operator override respected,
benign untouched), off-switch, and a LIVE header proof against the
actual vulnerable lab.
"""

import sys
from pathlib import Path
from unittest import mock

from suijin.modules.platform.lib import stealth


class TestIdentity:
    def test_no_scanner_strings_anywhere(self):
        ua = stealth.user_agent()
        for tell in ("suijin", "python", "scan", "bot", "curl", "wget", "spider"):
            assert tell not in ua.lower(), (tell, ua)

    def test_identity_sticky_and_full(self):
        a, b = stealth.browser_identity(), stealth.browser_identity()
        assert a == b  # sticky — UA-hopping per request is itself a tell
        assert {"User-Agent", "Accept", "Accept-Language", "Accept-Encoding", "Connection"} <= set(a)
        # browser-realistic header sets
        joined = " ".join(a)
        assert "sec-ch-ua" in joined or "Sec-Fetch" in joined

    def test_pool_all_plausible(self):
        for ua in stealth._UA_POOL:
            assert ua.startswith("Mozilla/5.0 (") and len(ua) > 60


class TestBurstLimiter:
    def test_manual_probe_pays_zero(self):
        # an LLM turn spaces requests by seconds — waiting none of it
        stealth._PACE_STATE["last"] = 100.0
        assert stealth.pacing_wait(now=103.0) == 0.0  # 3s later: go now

    def test_burst_gets_spaced(self):
        stealth._PACE_STATE["last"] = 100.0
        w = stealth.pacing_wait(now=100.1)  # 100ms later: machine-gun
        assert 0.2 < w <= stealth.MIN_GAP_S

    def test_first_ever_request_free(self):
        stealth._PACE_STATE["last"] = 0.0
        assert stealth.pacing_wait(now=50.0) == 0.0


class TestSanitizer:
    def test_nmap_capped(self):
        out = stealth.sanitize_command(["nmap", "-sV", "10.0.0.1"])
        assert "-T3" in out and "--max-rate" in out and "800" in out

    def test_gobuster_threaded(self):
        out = stealth.sanitize_command(["gobuster", "dir", "-u", "http://t", "-w", "x.txt"])
        assert "-t" in out and "8" in out

    def test_operator_override_respected(self):
        out = stealth.sanitize_command(["nmap", "-T5", "--max-rate", "2000", "10.0.0.1"])
        assert out == ["nmap", "-T5", "--max-rate", "2000", "10.0.0.1"]

    def test_idempotent(self):
        once = stealth.sanitize_command(["ffuf", "-u", "http://t", "-w", "w"])
        twice = stealth.sanitize_command(once)
        assert once == twice

    def test_benign_untouched(self):
        argv = ["echo", "hello world"]
        assert stealth.sanitize_command(argv) == argv

    def test_unknown_tool_untouched(self):
        argv = ["/opt/custom/scannerx", "--fast"]
        assert stealth.sanitize_command(argv) == argv

    def test_off_disables(self):
        with mock.patch.dict("os.environ", {"SUIJIN_STEALTH": "off"}):
            assert stealth.sanitize_command(["nmap", "10.0.0.1"]) == ["nmap", "10.0.0.1"]
            assert stealth.is_on() is False


class TestLiveHeaders:
    def test_http_request_sends_stealth_identity(self):
        """Live proof: boot the real lab, call the real http_request tool,
        inspect the lab's own traffic log for what we actually sent."""
        import json
        import subprocess
        import time

        import requests as rq

        log = Path("/tmp/blue_defend_traffic.jsonl")
        log.write_text("")
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve().parents[2] / "lab" / "blue_target" / "vulnerable_app.py")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**__import__("os").environ, "PORT": "5909"},
        )
        try:
            up = False
            for _ in range(40):
                try:
                    rq.get("http://127.0.0.1:5909/", timeout=1)
                    up = True
                    break
                except Exception:
                    time.sleep(0.25)
            assert up, "lab never came up on :5909"
            from suijin.modules.tools.lib.http_tools import http_request

            out = http_request("GET", "http://127.0.0.1:5909/api/login")
            assert not str(out).startswith("RATE LIMITED"), out
            entries = [json.loads(ln) for ln in log.read_text().splitlines()]
            sent = entries[-1]
            ua = sent.get("user_agent", "")
            assert "Mozilla/5.0" in ua and "suijin" not in ua.lower()
        finally:
            proc.kill()
