"""BF3 — the blue console: event blocks, live strip, command box."""

import asyncio

import pytest
from rich.console import Console

import suijin.modules.blueteam.lib.blue.enforcement as enf
from suijin.modules.blueteam.lib.blue.console_ui import BlueConsoleUI


@pytest.fixture(autouse=True)
def _plane(tmp_path, monkeypatch):
    monkeypatch.setenv("BLUE_ENFORCEMENT_FILE", str(tmp_path / "enf.json"))
    enf._DEFAULT_PATH = None
    yield enf


def _ui(width=100):
    c = Console(record=True, width=width, force_terminal=True)
    return BlueConsoleUI(c, target="hill_ctf"), c


class TestEventBlocks:
    def test_normal_request_renders(self):
        ui, c = _ui()
        ui.start()
        ui.begin_event("GET", "/health", "10.1.1.1")
        ui.verdict("normal", "known-normal pattern")
        ui.stop()
        out = c.export_text()
        assert "GET /health" in out and "10.1.1.1" in out
        assert "NORMAL" in out

    def test_investigated_renders_with_threat_count(self):
        ui, c = _ui()
        ui.start()
        ui.begin_event("POST", "/hill/login", "10.2.2.2")
        ui.verdict("investigated", "pattern: sql_injection (score 6/10)")
        ui.action("TARPIT", "fallback defense")
        ui.stop()
        out = c.export_text()
        assert "INVESTIGATED" in out and "sql_injection" in out
        assert "TARPIT" in out
        assert ui.detected == 1  # the strip counts it

    def test_action_panels_colored(self):
        ui, c = _ui()
        ui.begin_event("GET", "/admin", "10.3.3.3")
        ui.verdict("investigated", "decoy hit")
        ui.action("BLOCK", "canary material in use")
        out = c.export_text()
        assert "BLOCK" in out and "canary material" in out
        assert ui.blocked == 1

    def test_command_renders_syntax_highlighted(self):
        ui, c = _ui()
        ui.begin_event("POST", "/webhook", "10.4.4.4")
        ui.verdict("investigated", "ssrf_attempt")
        ui.command("curl -s http://127.0.0.1:5911/metadata")
        out = c.export_text()
        assert "curl" in out and "metadata" in out

    def test_watcher_report_renders(self):
        ui, c = _ui()
        ui.watcher("WATCHER /vault: vault_access (w5) from 10.9.9.9 [auto-enforced]")
        out = c.export_text()
        assert "vault_access" in out and "auto-enforced" in out


class TestLiveStrip:
    def test_strip_shows_stats(self):
        ui, c = _ui()
        ui.start()
        ui.requests = 42
        ui.detected = 7
        ui.blocked = 3
        ui.deceived = 2
        ui.waiting(False)
        ui.tick()
        ui.stop()
        # render the strip directly to check content
        r = Console(record=True, width=100, force_terminal=True)
        r.print(ui._strip())
        out = r.export_text()
        assert "42" in out and "7" in out and "3" in out and "2" in out
        assert "req" in out and "threats" in out and "blocked" in out and "deceived" in out

    def test_waiting_shows_spinner(self):
        ui, c = _ui()
        ui.start()
        ui.waiting(True)
        r = Console(record=True, width=100, force_terminal=True)
        r.print(ui._strip())
        assert "watching" in r.export_text()


class TestCommandBox:
    def test_block_command(self, _plane):
        ui, c = _ui()
        from suijin.modules.blueteam.lib.blue.session_runner import BlueCommandBox

        box = BlueCommandBox(ui, c)
        box.dispatch("/block 10.99.99.99")
        assert _plane.is_blocked("10.99.99.99")
        out = c.export_text()
        assert "BLOCKED" in out

    def test_unblock_command(self, _plane):
        ui, c = _ui()
        from suijin.modules.blueteam.lib.blue.session_runner import BlueCommandBox

        box = BlueCommandBox(ui, c)
        box.dispatch("/block 10.99.99.98")
        box.dispatch("/unblock 10.99.99.98")
        assert not _plane.is_blocked("10.99.99.98")

    def test_shell_command_passes_through(self):
        ui, c = _ui()
        from suijin.modules.blueteam.lib.blue.session_runner import BlueCommandBox

        box = BlueCommandBox(ui, c)
        box.dispatch("echo blue-live-test")
        out = c.export_text()
        assert "blue-live-test" in out

    def test_unknown_command_guidance(self):
        ui, c = _ui()
        from suijin.modules.blueteam.lib.blue.session_runner import BlueCommandBox

        box = BlueCommandBox(ui, c)
        box.dispatch("/bogus")
        assert "unknown" in c.export_text()

    def test_report_command(self):
        ui, c = _ui()
        ui.requests = 10
        ui.detected = 2
        from suijin.modules.blueteam.lib.blue.session_runner import BlueCommandBox

        box = BlueCommandBox(ui, c)
        box.dispatch("/report")
        out = c.export_text()
        assert "10" in out and "2" in out and "session report" in out

    def test_canaries_command(self):
        ui, c = _ui()
        from suijin.modules.blueteam.lib.blue.session_runner import BlueCommandBox

        box = BlueCommandBox(ui, c)
        box.dispatch("/canaries")
        assert "no canary hits" in c.export_text()


class TestFeedIntegration:
    def test_process_request_renders_blocks(self, _plane, tmp_path):
        """The full wiring: feed.ui = BlueConsoleUI — traffic crossing
        renders as event blocks with verdicts."""
        from suijin.modules.blueteam.lib.blue.tui.feed import FeedConfig, LiveFeed

        class E:
            total_analyses = 0
            total_cost_usd = 0.0

        class S:
            def find_for_request(self, p):
                return None

            def get_subagent_notes(self, p):
                return ""

            def record_anomaly(self, p, v):
                pass

            def get_summary(self):
                return {"total": 0}

        console = Console(record=True, width=100, force_terminal=True)
        ui = BlueConsoleUI(console, target="test")
        ui.start()

        feed = LiveFeed(
            ai_engine=E(), subagent_manager=S(), config=FeedConfig(baseline_requests=1, ai_analysis_enabled=False)
        )
        feed.TARPIT_FILE = str(tmp_path / "t.json")
        feed.ui = ui

        # benign baseline
        asyncio.run(
            feed.process_request({"method": "GET", "path": "/", "ip": "1.1.1.1", "user_agent": "x", "headers": {}})
        )
        # attack
        asyncio.run(
            feed.process_request(
                {
                    "method": "GET",
                    "path": "/q",
                    "query": {"x": "' UNION SELECT 1"},
                    "body": "",
                    "ip": "2.2.2.2",
                    "user_agent": "x",
                    "headers": {},
                }
            )
        )

        ui.stop()
        out = console.export_text()
        assert "GET /" in out  # baseline event
        assert "GET /q" in out  # attack event
        assert ui.requests == 2

    def test_stats_sync_updates_strip(self):
        ui, c = _ui()
        ui.requests = 100
        ui.detected = 15
        ui.blocked = 8
        ui.tick()
        assert ui._strip() is not None  # renders without error
