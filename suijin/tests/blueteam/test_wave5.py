"""Wave 5: E33-E40 — blue team ops."""

from suijin.modules.blueteam.lib.blue import ops


class TestAttackReplay:
    def test_red_trace_scored(self):
        trace = [
            {
                "tool_name": "http_request",
                "tool_args": {"url": "http://t/login", "body": "' OR 1=1--"},
                "success": True,
            },
            {"tool_name": "http_request", "tool_args": {"url": "http://t/", "body": ""}, "success": True},
        ]
        out = ops.replay_red_through_detector(trace)
        assert out["entries"] == 2 and "recall" in out


class TestDeception:
    def test_metrics(self):
        events = [{"event": "tarpit_engaged", "wasted_ms": 8000}, {"event": "honeypot_hit", "path": "/admin/fake"}]
        out = ops.deception_effectiveness(events)
        assert "1 tarpit hit(s)" in out and "8.0s" in out and "/admin/fake" in out


class TestPlaybooks:
    def test_register_and_fire(self):
        ops.register_playbook("sqli", ["block_ip", "notify_soc"])
        assert ops.run_playbook({"type": "sqli"}) == ["block_ip", "notify_soc"]
        assert ops.run_playbook({"type": "unknown"}) == []


class TestAllowlistAndFP:
    def test_add_check_fp_loop(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        assert "allowlisted" in ops.allowlist_add("/health", "uptime probe")
        assert "already" in ops.allowlist_add("/health")
        assert ops.allowlist_check({"path": "/health", "ip": "1.2.3.4"})
        assert not ops.allowlist_check({"path": "/admin", "ip": "1.2.3.4"})
        # FP feedback writes an allowlist entry
        out = ops.mark_false_positive({"type": "sqli", "path": "/api/ping"})
        assert "allowlisted" in out
        assert ops.allowlist_check({"path": "/api/ping", "ip": "x"})


class TestTimeline:
    def test_chronological(self):
        events = [
            {"ts": "2026-08-19T10:01:00Z", "kind": "response", "detail": "blocked 1.2.3.4"},
            {"ts": "2026-08-19T10:00:59Z", "kind": "detection", "detail": "sqli burst"},
        ]
        out = ops.incident_timeline(events)
        assert out.index("sqli burst") < out.index("blocked")


class TestLogAdapter:
    def test_nginx_combined(self):
        text = '1.2.3.4 - - [19/Aug/2026:10:00:00 +0000] "GET /login?id=1 HTTP/1.1" 200 512'
        entries = ops.parse_nginx_log(text)
        assert len(entries) == 1
        assert entries[0]["ip"] == "1.2.3.4" and entries[0]["path"] == "/login"
        assert ops.parse_nginx_log("garbage line") == []


class TestCanaries:
    def test_generate_and_watch(self):
        canaries = ops.generate_canaries(3)
        assert len(canaries) == 3 and all("canary" in c["value"] for c in canaries)
        trip = ops.watch_canary({"auth_user": canaries[1]["value"], "ip": "9.9.9.9"}, canaries)
        assert trip and "CANARY TRIPWIRE" in trip
        assert ops.watch_canary({"auth_user": "normal"}, canaries) is None
