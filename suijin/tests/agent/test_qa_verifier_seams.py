"""The embedded-harness seams (2026-09-11): the QA-verifier wiring.

Three seams, one measured incident behind each:
  1. run_red_team_async now seeds input_state["_run_config"] — a caller
     passing its own config dict was half-wired (the _think wrapper read
     it, the prompt builder did not, silently falling back to the on-disk
     config.json).
  2. recursion_limit honors max_iterations — it was hardcoded 100000,
     making the caller's iteration budget advisory-only.
  3. _with_install_hint fires on [COMMAND]-shaped missing-binary results,
     not just Error-prefixed ones — ~24 run_command-based tools (nmap,
     sqlmap, gobuster…) returned bare shell errors with no install hint.

Deterministic: no LLM, no network. The seams are pinned at their sources
(a wiring assertion — the behavior they unlock is covered by 4) and by
driving the real prompt builder and hint decorator directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import suijin.modules.agent.lib.agent_graph  # noqa: F401 — seam source-pinning
import suijin.modules.redteam.lib.redteamer


class TestQaVerifierProfile:
    def test_profile_exists_with_worklist_directive(self):
        from suijin.modules.agent.lib.profiles import PROFILES, get_profile, profile_directive

        assert "qa_verifier" in PROFILES
        p = get_profile({"adversary_profile": "qa_verifier"})
        assert p is not None
        assert p["pacing_delay_s"] == 0.0
        d = profile_directive({"adversary_profile": "qa_verifier"})
        assert "QA verifier" in d
        assert "A BUG IS CONFIRMED WHEN" in d
        assert "NAMING the defense" in d
        assert "empty worklist is success" in d

    def test_unknown_profile_still_none(self):
        from suijin.modules.agent.lib.profiles import get_profile

        assert get_profile({"adversary_profile": "nope"}) is None
        assert get_profile({}) is None


class TestSeeds:
    def test_async_entry_seeds_run_config(self):
        """The wiring assertion: the async entry's input_state carries
        _run_config, which prompts/base.py reads for adversary_profile/
        posture/mode_* keys. Without it a caller's dict is half-wired."""
        src = Path(suijin.modules.redteam.lib.redteamer.__file__).read_text(encoding="utf-8")
        assert '"_run_config": _merge_profile_overrides(dict(config or {}))' in src

    def test_recursion_limit_honors_max_iterations(self):
        """The caller's budget is real: recursion_limit derives from
        max_iterations (the hardcoded 100000 made it advisory-only)."""
        src = Path(suijin.modules.redteam.lib.redteamer.__file__).read_text(encoding="utf-8")
        assert '"recursion_limit": min(_iter_budget * 2, 1_000_000)' in src

    def test_prompt_builder_reads_seeded_run_config(self):
        """The behavior the seeding unlocks: state carrying _run_config
        with an adversary_profile produces the directive in the built
        prompt — the same path the embedded harness now drives."""
        from suijin.modules.agent.lib.prompts.base import build_agent_system_prompt

        state = {
            "_objective": "verify the worklist",
            "_run_config": {"adversary_profile": "qa_verifier"},
            "current_phase": "recon",
            "messages": [],
            "execution_trace": [],
        }
        prompt = build_agent_system_prompt(state)
        assert "QA verifier" in prompt
        assert "empty worklist is success" in prompt


class TestInstallHintCommandShape:
    """Tier-B: run_command-based tools format as [COMMAND]… with the
    missing binary in stderr/exit — the hint must fire there too, and
    must NOT fire on successful output that merely says 'not found'."""

    def _hint(self, text):
        from suijin.modules.tools.lib.dispatch import _with_install_hint

        return _with_install_hint("nmap_scan", text)

    def test_exit_127_gets_hint(self):
        result = "[COMMAND] nmap -sV target\n[EXIT] 127 (0.1s)\n[STDERR]\nsh: nmap: command not found\n"
        out = self._hint(result)
        assert "→ install:" in out

    def test_no_such_file_gets_hint(self):
        result = "[COMMAND] sqlmap -u x\n[EXIT] 127 (0.0s)\n[STDERR]\nNo such file or directory\n"
        out = self._hint(result)
        assert "→ install:" in out

    def test_successful_output_with_not_found_text_gets_no_hint(self):
        """The false-positive guard: a successful scan whose output
        happens to say 'not found' must NOT sprout an install hint."""
        result = "[COMMAND] nmap -sV target\n[EXIT] 0 (3.2s)\n[STDOUT]\n0 hosts found; service not found on port 8\n"
        out = self._hint(result)
        assert "→ install:" not in out

    def test_error_prefixed_shape_still_works(self):
        out = self._hint("Error: nmap not installed")
        assert "→ install:" in out


class TestUnderdeclaredManifests:
    @pytest.mark.parametrize(
        "pack,expected",
        [
            ("adenum", {"impacket", "ldapsearch", "smbclient"}),
            ("iptables_control", {"sudo", "iptables"}),
        ],
    )
    def test_deps_declared(self, pack, expected):
        """Tier-C closes: the packs whose external binaries were invisible
        to missing_binaries()/doctor/hints now declare them."""
        import json

        p = Path(__file__).resolve().parents[2] / "modules" / pack / "manifest.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        assert expected.issubset(set(d.get("dependencies") or []))


class TestSpeedDials:
    """The one-hour diagnosis fixes (2026-09-12): the foothold doctrine
    exempts qa_verifier; supervision cadences are config keys; the context
    cap is config-driven. Each is pinned at its seam."""

    def test_foothold_exempt_for_qa_verifier(self):
        from suijin.modules.agent.lib.mode_governor import govern

        state = {"current_phase": "exploitation", "_foothold_at": True}
        # operator behavior unchanged: forced post_exploit
        patch = govern(state, {"posture": "assertive"})
        assert patch and patch.get("current_phase") == "post_exploitation"
        # the QA profile: no forced transition — creds are a finding, not a foothold
        patch = govern(state, {"posture": "assertive", "adversary_profile": "qa_verifier"})
        assert patch is None or patch.get("current_phase") != "post_exploitation"

    def test_cadence_keys_exist(self):
        """oracle/drift/deep-supervisor cadences read their config keys;
        0 = off. Source-pinned (the cadence sites are inline in _think)."""
        src = Path(suijin.modules.agent.lib.agent_graph.__file__).read_text(encoding="utf-8")
        assert 'get("oracle_interval", 4)' in src
        assert 'get("drift_interval", 7)' in src
        assert 'get("supervisor_deep_interval", 15)' in src

    def test_context_cap_configurable(self):
        from suijin.modules.agent.lib.agent_graph import _merge_state

        msgs = [{"role": "user", "content": str(i)} for i in range(30)]
        left = {"_run_config": {"context_cap": 12}, "messages": list(msgs)}
        right = {"messages": [msgs[-1]]}
        merged = _merge_state(left, right)
        assert len(merged["messages"]) == 12
        # default unchanged
        left2 = {"messages": list(msgs)}
        merged2 = _merge_state(left2, {"messages": [msgs[-1]]})
        assert len(merged2["messages"]) == 25

    def test_speed_dials_reach_the_provider_and_graph(self):
        """The dials the engine harness sets are all keys the engine
        actually consumes — intelligence (provider thinking toggle),
        oracle/drift/deep cadences, context_cap (merge). If any key drifts
        out of the graph/provider code, this catches the dead dial."""
        import suijin.modules.agent.lib.agent_graph as ag
        import suijin.modules.providers.lib as prov

        ag_src = Path(ag.__file__).read_text(encoding="utf-8")
        prov_src = Path(prov.__file__).read_text(encoding="utf-8")
        assert '"context_cap"' in ag_src
        assert '"oracle_interval"' in ag_src and '"drift_interval"' in ag_src
        assert '"intelligence"' in prov_src  # the thinking toggle's config key


class TestCoveragePressure:
    """Coverage Without Captivity: awareness messages, no path control."""

    def _state(self, elapsed_min, wall=20):
        import time as _t

        wall_s = wall * 60.0
        return {
            "_run_config": {"wall_minutes": wall},
            # deadline placed so `elapsed_min` of wall clock has passed
            "_wall_deadline": _t.monotonic() + wall_s - elapsed_min * 60.0,
            "_attack_queue": [
                {"surface": "app.py:97 sql_injection", "tried": False},
                {"surface": "app.py:182 template_injection", "tried": False},
                {"surface": "http://t/login", "tried": True},
            ],
        }

    def test_halfway_fires_once_with_tokens(self):
        from suijin.modules.agent.lib.mode_governor import coverage_pressure

        st, res = self._state(11), {}
        coverage_pressure(st, res, st["_attack_queue"])
        msgs = res.get("messages") or []
        assert len(msgs) == 1
        assert "CHECK-IN" in msgs[0]["content"] or "PRESSURE" in msgs[0]["content"]
        assert "sql_injection" in msgs[0]["content"]
        assert len(res["_pressure_sent"]) >= 1

    def test_escalation_replaces_not_stacks(self):
        from suijin.modules.agent.lib.mode_governor import coverage_pressure

        st, res = self._state(16), {}
        st["_pressure_sent"] = [8.0]
        coverage_pressure(st, res, st["_attack_queue"])
        msgs = res.get("messages") or []
        assert len(msgs) == 1 and "INSANE" not in msgs[0]["content"]
        assert "min" in msgs[0]["content"]
        assert len(res["_pressure_sent"]) == 2

    def test_final_level_names_verdict_options(self):
        from suijin.modules.agent.lib.mode_governor import coverage_pressure

        st, res = self._state(29, wall=31), {}
        coverage_pressure(st, res, st["_attack_queue"])
        assert "INSANE" in res["messages"][0]["content"]
        assert "REPORT" in res["messages"][0]["content"]

    def test_ladder_texts_are_the_owners_words(self):
        from suijin.modules.agent.lib import mode_governor as mg

        texts = dict(zip((l for l, _ in mg._PRESSURE_LADDER), (t for _, t in mg._PRESSURE_LADDER)))
        assert "You done?" in texts[5.0]
        assert "Faster" in texts[8.0]
        assert "get on with it" in texts[11.0]
        assert max(l for l, _ in mg._PRESSURE_LADDER) < 30.0  # the wall kills at 30

    def test_silent_without_deadline(self):
        from suijin.modules.agent.lib.mode_governor import coverage_pressure

        st, res = {"_attack_queue": [{"surface": "x", "tried": False}]}, {}
        coverage_pressure(st, res, st["_attack_queue"])
        assert "messages" not in res

    def test_silent_when_all_examined(self):
        from suijin.modules.agent.lib.mode_governor import coverage_pressure

        st = self._state(0.95)
        st["_attack_queue"] = [{"surface": "x", "tried": True}]
        res = {}
        coverage_pressure(st, res, st["_attack_queue"])
        assert "messages" not in res

    def test_early_time_no_pressure(self):
        from suijin.modules.agent.lib.mode_governor import coverage_pressure

        st, res = self._state(0.10), {}
        coverage_pressure(st, res, st["_attack_queue"])
        assert "messages" not in res
