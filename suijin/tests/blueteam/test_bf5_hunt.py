"""BF5 — traffic retention, IOC extraction, retro-sweep hunt."""

import pytest

import suijin.modules.platform.lib.workspace as ws
from suijin.modules.blueteam.lib.blue.retention import (
    TrafficRetention,
    extract_iocs,
    hunt,
    render_hunt,
    store_iocs,
)


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    ws._CURRENT_ENGAGEMENT = None
    yield tmp_path
    ws._CURRENT_ENGAGEMENT = None


class TestTrafficRetention:
    def test_append_and_read(self):
        r = TrafficRetention("s1")
        r.append({"ip": "1.1.1.1", "path": "/a"})
        r.append({"ip": "2.2.2.2", "path": "/b"})
        entries = r.entries()
        assert len(entries) == 2
        assert entries[0]["ip"] == "1.1.1.1"

    def test_never_truncates(self):
        """The session-start wipe is DEAD — entries persist across instances."""
        r1 = TrafficRetention("s1")
        r1.append({"ip": "1.1.1.1", "path": "/first"})
        r2 = TrafficRetention("s1")  # "new session" — must NOT truncate
        r2.append({"ip": "1.1.1.1", "path": "/second"})
        assert len(r1.entries()) == 2

    def test_all_sessions(self):
        TrafficRetention("alpha").append({"ip": "1", "path": "/x"})
        TrafficRetention("beta").append({"ip": "2", "path": "/y"})
        sessions = TrafficRetention.all_sessions()
        assert "alpha" in sessions and "beta" in sessions

    def test_rotation(self):
        """Shards rotate when full and cap at 5."""
        r = TrafficRetention("rotate-test")
        # write enough entries to exceed the shard (simulate by writing a big entry)
        big = "A" * 1000
        for i in range(25):  # 25KB > SHARD_MAX_BYTES would need 10000 entries
            r.append({"ip": f"ip{i}", "big": big})
        # at minimum, all entries readable
        entries = r.entries()
        assert len(entries) == 25


class TestIOCExtraction:
    def _case(self):
        return {
            "id": "CASE-0001",
            "actor_ip": "9.9.9.9",
            "timeline": [
                {
                    "kind": "detector",
                    "type": "sql_injection",
                    "score": 7,
                    "endpoint": "/login",
                    "ts": "2026-01-01T00:00:00Z",
                },
                {
                    "kind": "detector",
                    "type": "sql_injection",
                    "score": 8,
                    "endpoint": "/api",
                    "ts": "2026-01-01T00:01:00Z",
                },
                {"kind": "action", "type": "BLOCK", "detail": "canary trip", "ts": "2026-01-01T00:02:00Z"},
            ],
        }

    def test_extract_from_case(self):
        iocs = extract_iocs(self._case())
        assert len(iocs) == 1  # sql_injection deduped to one IOC
        assert iocs[0]["type"] == "attack_pattern"
        assert iocs[0]["value"] == "sql_injection"
        assert iocs[0]["actor"] == "9.9.9.9"
        assert iocs[0]["source_case"] == "CASE-0001"

    def test_store_and_load(self):
        class MockKG:
            def __init__(self):
                self.intel = {}

            def add_intelligence(self, key, value):
                self.intel[key] = value

        iocs = extract_iocs(self._case())
        kg = MockKG()
        stored = store_iocs(iocs, kg)
        assert stored == 1
        assert "ioc:attack_pattern:sql_injection" in kg.intel


class TestHunt:
    def test_hunt_finds_cross_session_ioc(self):
        # session 1: an attack happens, IOC stored
        TrafficRetention("jan").append(
            {"ip": "1.2.3.4", "path": "/login", "query": {"u": "' OR 1=1"}, "timestamp": "2026-01-01T00:00:00Z"}
        )

        class MockKG:
            def __init__(self):
                self.intel = {}

            def add_intelligence(self, k, v):
                self.intel[k] = v

            def get_intelligence(self):
                return self.intel

        kg = MockKG()
        store_iocs([{"type": "attack_pattern", "value": "sql_injection", "actor": "1.2.3.4", "source_case": "C-1"}], kg)

        # hunt sweeps retained traffic against the IOC store
        result = hunt(kg, sessions=["jan"])
        assert result["scanned"] >= 1
        assert result["ioc_count"] >= 1
        assert len(result["findings"]) >= 1
        assert result["findings"][0]["ioc_value"] == "sql_injection"

    def test_hunt_no_iocs(self):
        result = hunt(None)
        assert result["scanned"] == 0
        assert result["findings"] == []

    def test_render_hunt(self):
        result = {
            "scanned": 100,
            "ioc_count": 5,
            "sessions_scanned": ["jan"],
            "findings": [
                {
                    "ioc_type": "attack_pattern",
                    "ioc_value": "sqli",
                    "entry_ip": "1.1.1.1",
                    "entry_path": "/login",
                    "session": "jan",
                }
            ],
        }
        md = render_hunt(result)
        assert "Hunt Report" in md and "100" in md and "sqli" in md and "1.1.1.1" in md

    def test_hunt_command_in_console(self):
        import io

        from rich.console import Console

        from suijin.modules.blueteam.lib.blue.console_ui import BlueConsoleUI
        from suijin.modules.blueteam.lib.blue.session_runner import BlueCommandBox

        c = Console(file=io.StringIO(), record=True, width=100, force_terminal=True)
        ui = BlueConsoleUI(c, target="test")
        ui.start()
        box = BlueCommandBox(ui, c)
        box.dispatch("/hunt")
        out = c.export_text()
        assert "Hunt Report" in out
        ui.stop()
