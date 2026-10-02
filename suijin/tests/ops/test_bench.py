"""Bench — graded lab runs: flag capture through real exploit paths."""

import asyncio
import json

import pytest

from suijin.modules.ops.lib.bench import (
    LAB_FLAGS,
    LAB_PORTS,
    _extract_flags,
    _last_token,
    _mock_generate,
    _mock_script,
    bench_history,
    render_history,
    run_bench,
)


class TestBenchUnit:
    def test_flag_extraction(self):
        out = 'Status: 200\nBody:\n{"flag": "FLAG{abc_123}"} and FLAG{second}'
        assert _extract_flags(out) == {"FLAG{abc_123}", "FLAG{second}"}

    def test_unknown_lab_errors(self):
        s = run_bench("nope")
        assert "error" in s and "nope" in s["error"]

    def test_flag_inventory_matches_ports(self):
        assert set(LAB_FLAGS) == set(LAB_PORTS)
        for lab, flags in LAB_FLAGS.items():
            assert flags and all(f.startswith("FLAG{") and f.endswith("}") for f in flags), lab

    @pytest.mark.parametrize("lab", sorted(LAB_FLAGS))
    def test_script_shapes(self, lab):
        script = _mock_script(lab, 6100)
        assert script, lab
        assert script[-1]["action"] == "complete"
        for turn in script[:-1]:
            assert turn["action"] == "use_tool"
            assert turn["tool_name"] == "http_request"
            assert turn["tool_args"]["url"].startswith("http://127.0.0.1:6100")

    @pytest.mark.parametrize("lab", sorted(LAB_FLAGS))
    def test_scripts_never_embed_flags(self, lab):
        """Anti-cheat: the scripted agent must not smuggle flag values in
        requests — every scored flag has to come back from the lab itself."""
        blob = json.dumps(_mock_script(lab, 6100))
        for flag in LAB_FLAGS[lab]:
            assert flag not in blob, (lab, flag)

    def test_last_token_takes_newest(self):
        """Tool results arrive inside one big trace message — a naive
        reverse scan keeps re-finding the OLDEST token. The newest must win."""
        msgs = [
            {
                "role": "system",
                "content": 'trace: access_token":"AAAA_token_aaaaaa" then access_token":"BBBB_token_bbbbbb',
            },
            {"role": "user", "content": "Proceed"},
        ]
        assert _last_token(msgs) == "BBBB_token_bbbbbb"

    def test_mock_generate_discriminates_callers(self):
        script = [
            {
                "action": "use_tool",
                "tool_name": "http_request",
                "tool_args": {"url": "http://x/{{TOKEN}}"},
                "thought": "t",
            },
            {"action": "complete", "completion_reason": "done", "thought": "t"},
        ]
        gen = _mock_generate(script)
        # auxiliary callers (supervisor/oracle pass prompt= kwarg or a bare string) — never consume
        assert asyncio.run(gen(prompt="guidance?", system="x")) == ""
        assert asyncio.run(gen("plain prompt string")) == ""
        # agent turns: token placeholder filled from prior tool output
        msgs = [{"role": "system", "content": 'TOOL RESULT: access_token":"Zk9_new_token_zz01"'}]
        first = json.loads(asyncio.run(gen(msgs, {})))
        assert first["tool_args"]["url"] == "http://x/Zk9_new_token_zz01"
        second = json.loads(asyncio.run(gen(msgs, {})))
        assert second["action"] == "complete"
        assert asyncio.run(gen(msgs, {})) == ""  # exhausted

    def test_history_roundtrip(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        ws._reset_engagement()  # hermetic: no engagement pinned from another test
        assert bench_history() == []
        assert "No bench runs" in render_history()
        from suijin.modules.ops.lib.bench import _append_history

        _append_history(
            {
                "lab": "unit-fixture",
                "mode": "mock",
                "timestamp": "2026-01-01T00:00:00Z",
                "flags_known": 1,
                "flags_captured": 1,
                "capture_rate": 1.0,
                "tool_calls": 3,
                "cost_usd": 0.0,
            }
        )
        hist = bench_history()
        assert len(hist) == 1 and hist[0]["lab"] == "unit-fixture"
        rendered = render_history()
        assert "unit-fixture" in rendered and "flags 1/1" in rendered


@pytest.mark.slow
class TestBenchMockEndToEnd:
    """Full pipeline per lab: boot lab, run scripted agent via the real
    graph + dispatch, score flags. Every captured flag left the lab through
    a real exploit path (nothing is scripted into the requests)."""

    @pytest.mark.parametrize("lab,expected_calls", [("northbridge", 4)])
    def test_full_capture(self, lab, expected_calls, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        s = run_bench(lab, mock=True)
        assert "error" not in s, s
        # the northbridge mock walks TIER-0 ONLY — the crowns and the
        # authenticated tiers are out of scripted reach BY DESIGN (that
        # is what the real agent is for); every flag the mock COULD
        # reach, it captured
        tier0 = {"FLAG{nb_tier_gitmap}", "FLAG{nb_tier_backup}"}
        assert set(s["flags_detail"]) == tier0, s["flags_detail"]
        assert not any("crown" in f for f in s["flags_detail"])
        assert s["tool_calls"] == expected_calls
        assert s["mode"] == "mock" and s["cost_usd"] == 0.0
        # history persisted under the patched workspace
        hist = bench_history()
        assert hist and hist[-1]["lab"] == lab
