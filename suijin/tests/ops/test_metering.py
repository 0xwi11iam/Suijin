"""Metering, failover telemetry, prompt profiler (D28/D29/D30/D31)."""

import json
from unittest import mock

from suijin.modules.agent.lib.profiler import profile_messages, record, render
from suijin.modules.ops.lib.metering import forecast, leaderboard


def _trail(tmp_path, name, cost, actions, findings):
    from suijin.modules.platform.lib.workspace import artifact_dir

    d = artifact_dir("audit_trails")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(
        json.dumps(
            {
                "engagement": name,
                "cost_usd": cost,
                "total_actions": actions,
                "successful_actions": actions,
                "findings": [{"severity": "high"}] * findings,
                "ended": "2026-08-19T10:00:00+00:00",
            }
        )
    )


class TestLeaderboard:
    def test_leaderboard_ranks_findings_per_dollar(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        _trail(tmp_path, "rich", 1.0, 10, 5)
        _trail(tmp_path, "poor", 5.0, 50, 1)
        out = leaderboard()
        assert "2 engagements" in out and "$6.00 total" in out
        assert out.index("rich") < out.index("poor")  # ranked by findings/$

    def test_empty_history(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        assert "No completed engagements" in leaderboard()


class TestForecast:
    def test_needs_two_priced(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        _trail(tmp_path, "one", 1.0, 5, 0)
        assert "Not enough priced history" in forecast()

    def test_forecast_projects(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        _trail(tmp_path, "a", 2.0, 100, 3)
        _trail(tmp_path, "b", 4.0, 200, 5)
        out = forecast(action_count=150)
        assert "mean $3.00" in out and "150 actions" in out


class TestFailoverTelemetry:
    def test_counters_track_chain_outcomes(self):
        from suijin.modules.providers import lib as providers

        stats = providers.FAILOVER_STATS
        before = dict(stats)
        try:
            # primary ok
            with mock.patch.object(providers, "generate", return_value="ok"):
                assert providers.generate_with_failover([], {"provider": "zai"}) == "ok"
            assert stats["chains"] == before["chains"] + 1
            assert stats["primary_ok"] == before["primary_ok"] + 1
            # failover to second
            calls = {"n": 0}

            def flaky(messages, config=None, **kw):
                calls["n"] += 1
                return "Error: down" if calls["n"] == 1 else "recovered"

            with mock.patch.object(providers, "generate", side_effect=flaky):
                assert (
                    providers.generate_with_failover([], {"provider": "zai", "fallback_providers": ["deepseek"]})
                    == "recovered"
                )
            assert stats["failovers"] == before["failovers"] + 1
            assert "FAILOVER" in stats["last_event"]
        finally:
            for k, v in before.items():
                stats[k] = v


class TestProfiler:
    def test_profile_breakdown(self):
        msgs = [
            {"role": "system", "content": "x" * 4000},
            {"role": "user", "content": "y" * 800},
            {"role": "assistant", "content": "z" * 200},
        ]
        prof = profile_messages(msgs)
        assert prof["system_chars"] == 4000
        assert prof["history_chars"] == 1000
        assert prof["est_tokens"] == 1250

    def test_record_trend_and_render(self):
        state = {"messages": [{"role": "system", "content": "a" * 2000}]}
        record(state)
        state["messages"].append({"role": "user", "content": "b" * 2000})
        record(state)
        assert len(state["_prompt_profile_trend"]) == 2
        out = render(state)
        assert "500 tok" in out.replace(",", "") or "est tokens" in out
        assert "growth" in out
