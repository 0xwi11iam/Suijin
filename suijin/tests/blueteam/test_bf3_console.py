"""BF3/BF3.5 — the blue console: clean strip, silent normals, live input box.

Operator contract (BF3.5):
  - benign (NORMAL) traffic prints NOTHING (strip req counter ticks)
  - baseline training is strip-only (`baseline N/M` stat, no lines)
  - a request occupies the transient watching row until its verdict
    lands, then the row auto-deletes
  - the input box row shows live typing with a block cursor
  - the clock ticks every second in the stats row
"""

import asyncio

import pytest
from rich.console import Console

import suijin.modules.blueteam.lib.blue.enforcement as enf
from suijin.modules.blueteam.lib.blue.console_ui import INPUT_HINT, BlueConsoleUI


@pytest.fixture(autouse=True)
def _plane(tmp_path, monkeypatch):
    monkeypatch.setenv("BLUE_ENFORCEMENT_FILE", str(tmp_path / "enf.json"))
    enf._DEFAULT_PATH = None
    yield enf


def _sink_console(width=100):
    """A console writing to an in-memory buffer — force_terminal without
    leaking ANSI to the real stdout (CI kernel quiet-boot tests capture
    stdout globally and the strip's control codes broke them)."""
    import io

    return Console(file=io.StringIO(), record=True, width=width, force_terminal=True)


def _ui(width=100):
    c = _sink_console(width)
    return BlueConsoleUI(c, target="hill_ctf"), c


class TestCleanConsole:
    """BF3.5: only detections scroll the console."""

    def test_normal_request_is_silent(self):
        ui, c = _ui()
        ui.start()
        ui.begin_event("GET", "/health", "10.1.1.1")
        ui.verdict("normal", "known-normal pattern")
        ui.stop()
        out = c.export_text()
        assert "GET /health" not in out  # nothing printed
        assert "NORMAL" not in out
        assert ui.requests == 1  # the counter still ticked

    def test_anomalous_is_one_gold_line(self):
        ui, c = _ui()
        ui.begin_event("GET", "/odd", "10.1.1.2")
        ui.verdict("anomalous", "AI says benign (score 3)")
        out = c.export_text()
        assert "/odd" in out and "10.1.1.2" in out
        assert out.count("\n") <= 3  # ONE line, not a block

    def test_investigated_renders_full_block(self):
        ui, c = _ui()
        ui.begin_event("POST", "/hill/login", "10.2.2.2")
        ui.verdict("investigated", "pattern: sql_injection (score 6/10)")
        ui.action("TARPIT", "fallback defense")
        out = c.export_text()
        assert "POST /hill/login" in out and "10.2.2.2" in out
        assert "INVESTIGATED" in out and "sql_injection" in out
        assert "TARPIT" in out
        assert ui.detected == 1

    def test_watching_row_lifecycle(self):
        ui, c = _ui()
        ui.begin_event("GET", "/api/v2", "10.4.4.4")
        strip = ui.render_strip_text()
        assert "GET /api/v2" in strip and "10.4.4.4" in strip
        # prints nothing to the transcript while watching
        assert "GET /api/v2" not in c.export_text()
        # verdict lands -> the watching row auto-deletes
        ui.verdict("normal", "known-normal")
        assert "GET /api/v2" not in ui.render_strip_text()

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
    def test_strip_shows_stats_and_clock(self):
        ui, c = _ui()
        ui.start()
        ui.requests = 42
        ui.detected = 7
        ui.blocked = 3
        ui.deceived = 2
        ui.waiting(False)
        ui.tick()
        ui.stop()
        strip = ui.render_strip_text(100)
        assert "42" in strip and "7" in strip and "3" in strip and "2" in strip
        assert "req" in strip and "threats" in strip and "blocked" in strip and "deceived" in strip
        assert ":" in strip  # the live clock (MM:SS)

    def test_waiting_shows_spinner(self):
        ui, c = _ui()
        ui.start()
        ui.waiting(True)
        ui.stop()
        assert "watching" in ui.render_strip_text()

    def test_baseline_stat_is_strip_only(self):
        ui, c = _ui()
        ui.baseline_stat(2, 25)
        assert "baseline 2/25" in ui.render_strip_text()
        assert "baseline" not in c.export_text()  # ZERO console lines
        assert ui.requests == 1  # the counter ticked
        ui.baseline_done()
        assert "baseline" not in ui.render_strip_text()

    def test_input_box_row(self):
        ui, c = _ui()
        assert INPUT_HINT in ui.render_strip_text(140)  # idle: the hint
        ui.set_input("/block 10.")
        strip = ui.render_strip_text(140)
        assert "/block 10." in strip and "»" in strip  # live typing + cursor row
        ui.set_input(None)
        assert INPUT_HINT in ui.render_strip_text(140)


class TestKeystrokeEditor:
    """The pure editor contract behind the real input box."""

    def test_typing_accumulates(self):
        from suijin.modules.blueteam.lib.blue.session_runner import _KeystrokeReader

        buf = ""
        for ch in "/state":
            buf, action = _KeystrokeReader.apply_key(buf, ch)
            assert action is None
        assert buf == "/state"

    def test_backspace_and_clear(self):
        from suijin.modules.blueteam.lib.blue.session_runner import _KeystrokeReader

        buf, _ = _KeystrokeReader.apply_key("hell", "\x7f")
        assert buf == "hel"
        buf, _ = _KeystrokeReader.apply_key("hello", "\x15")
        assert buf == ""

    def test_enter_flushes(self):
        from suijin.modules.blueteam.lib.blue.session_runner import _KeystrokeReader

        buf, action = _KeystrokeReader.apply_key("hello", "\r")
        assert action == "line" and buf == ""

    def test_arrows_and_ctrl_c(self):
        from suijin.modules.blueteam.lib.blue.session_runner import _KeystrokeReader

        buf, action = _KeystrokeReader.apply_key("abc", "\x1b[D")
        assert buf == "abc" and action is None  # arrows ignored
        buf, action = _KeystrokeReader.apply_key("abc", "\x03")
        assert action == "intr" and buf == "abc"

    def test_buffer_cap(self):
        from suijin.modules.blueteam.lib.blue.session_runner import _KeystrokeReader

        buf = "x" * 130
        buf, _ = _KeystrokeReader.apply_key(buf, "y")
        assert len(buf) == 120  # hard-capped at 120


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

    def test_dispatch_resets_input_box(self):
        ui, c = _ui()
        from suijin.modules.blueteam.lib.blue.session_runner import BlueCommandBox

        box = BlueCommandBox(ui, c)
        ui.set_input("/state")
        box.dispatch("/state")
        strip = ui.render_strip_text(140)
        assert INPUT_HINT in strip  # box back to the hint after dispatch


class TestFeedIntegration:
    def _feed(self, ui, tmp_path, baseline_requests=1):
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

        feed = LiveFeed(
            ai_engine=E(),
            subagent_manager=S(),
            config=FeedConfig(baseline_requests=baseline_requests, ai_analysis_enabled=False, show_all_normals=False),
        )
        feed.TARPIT_FILE = str(tmp_path / "t.json")
        feed.ui = ui
        return feed

    def test_process_request_renders_blocks(self, _plane, tmp_path):
        """Full wiring: benign traffic is silent, the attack materializes."""
        console = _sink_console(100)
        ui = BlueConsoleUI(console, target="test")
        ui.start()
        feed = self._feed(ui, tmp_path, baseline_requests=1)

        # first benign request establishes baseline (rid=1 >= 1)
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
        assert "GET /q" in out  # the attack rendered its block
        assert "GET /" not in out.replace("GET /q", "")  # the benign one printed nothing
        assert ui.requests == 2
        assert ui.detected == 1

    def test_baseline_training_is_strip_only(self, _plane, tmp_path):
        """Training requests: zero console lines, strip stat only, one note
        when baseline establishes."""
        console = _sink_console(100)
        ui = BlueConsoleUI(console, target="test")
        ui.start()
        feed = self._feed(ui, tmp_path, baseline_requests=3)

        for i in range(3):  # rid 1,2 train; rid 3 establishes
            asyncio.run(
                feed.process_request(
                    {"method": "GET", "path": f"/b{i}", "ip": "1.1.1.1", "user_agent": "x", "headers": {}}
                )
            )

        ui.stop()
        out = console.export_text()
        assert "learning baseline" not in out  # the old spam is dead
        assert "baseline training" not in out
        assert "GET /b0" not in out and "GET /b1" not in out  # silent training
        assert "baseline established" in out  # the single completion note
        assert ui.requests == 3

    def test_stats_sync_updates_strip(self):
        ui, c = _ui()
        ui.requests = 100
        ui.detected = 15
        ui.blocked = 8
        ui.tick()
        assert ui._strip() is not None  # renders without error
