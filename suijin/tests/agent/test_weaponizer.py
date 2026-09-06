"""Weaponization engine — foothold, weaponizer proposals, positive memory,
chain planner, payload mutation."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from suijin.modules.agent.lib.mode_governor import govern, scoreboard, update_foothold  # noqa: E402
from suijin.modules.agent.lib.weaponizer import propose_for  # noqa: E402


def _st(phase="informational", **kw):
    base = {"current_phase": phase, "current_iteration": 8, "_attack_queue": [], "execution_trace": []}
    base.update(kw)
    return base


class TestFoothold:
    def test_uid_output_sets_foothold(self):
        st = _st()
        r = {"_current_step": {"tool_name": "http_request", "tool_output": "uid=0(root) gid=0(root)"}}
        assert update_foothold(st, r) is True
        assert r["_foothold_at"] is True

    def test_cred_capture_sets_foothold(self):
        st = _st()
        r = {"_current_step": {"tool_name": "read_file", "tool_output": 'password = "Sup3rS3cret!"'}}
        assert update_foothold(st, r) is True

    def test_quiet_output_does_not(self):
        st = _st()
        r = {"_current_step": {"tool_name": "http_request", "tool_output": "200 OK hello"}}
        assert update_foothold(st, r) is False

    def test_once_only(self):
        st = _st(_foothold_at=True)
        r = {"_current_step": {"tool_output": "uid=0(root)"}}
        assert update_foothold(st, r) is False

    def test_foothold_forces_post_exploit(self):
        st = _st(
            phase="exploitation",
            _foothold_at=True,
            _attack_queue=[{"surface": "x", "cls": "web", "tried": True, "iter": 1}],
        )
        d = govern(st, {})
        assert d is not None
        assert d["current_phase"] == "post_exploitation"
        assert d["attack_path_type"] == "post_exploit"
        # idempotent: second call with _post_exploit_done returns None
        st["_post_exploit_done"] = True
        assert govern(st, {}) is None

    def test_scoreboard_shows_foothold(self):
        sb = scoreboard(_st(_foothold_at=True))
        assert "FOOTHOLD" in sb


class TestWeaponizer:
    def test_confirmed_catalog_proposes(self):
        r = {
            "_current_step": {
                "tool_name": "catalog_exploit",
                "tool_output": "EXP-007 CONFIRMED class=sqli title='login bypass' marker reproduced",
            }
        }
        prop = propose_for(r, set())
        assert prop is not None
        msg, key = prop
        assert "ESCALATION READY" in msg and "EXP-007" in msg
        assert "deploy_subagent" in msg
        assert "sql" in msg.lower()

    def test_proposed_once_per_finding(self):
        r = {"_current_step": {"tool_name": "catalog_exploit", "tool_output": "EXP-007 CONFIRMED sqli"}}
        assert propose_for(r, {"EXP-007"}) is None

    def test_non_confirmed_no_proposal(self):
        r = {"_current_step": {"tool_name": "catalog_exploit", "tool_output": "EXP-007 FAILED_REPRO sqli"}}
        assert propose_for(r, set()) is None

    def test_class_playbooks_covered(self):
        for cls in ("sql", "ssrf", "upload", "jwt", "xxe", "race"):
            r = {"_current_step": {"tool_name": "catalog_exploit", "tool_output": f"EXP-0{len(cls)} CONFIRMED {cls}"}}
            assert propose_for(r, set()) is not None, cls


class TestPositiveMemory:
    def test_target_key_extracts_host(self):
        from suijin.modules.agent.lib.attack_memory import target_key

        assert target_key("hack https://arbonia.com/login deeply") == "arbonia.com"
        assert target_key("test http://10.0.0.5:8000/api") == "10.0.0.5:8000"
        assert target_key("plain words only") == "plain words only"[:60]

    def test_what_worked_reads_confirmed(self, tmp_path, monkeypatch):
        """The REAL catalog shape: entries keyed EXP-### (a DICT, as
        exploit_catalog writes it) — the old fixture wrote a list and
        masked the dict/list reader bug that killed what_worked in prod."""
        from suijin.modules.agent.lib import attack_memory as am

        eng = tmp_path / "exploits" / "run1"
        eng.mkdir(parents=True)
        (eng / "catalog.json").write_text(
            json.dumps(
                {
                    "entries": {
                        "EXP-001": {
                            "id": "EXP-001",
                            "status": "CONFIRMED",
                            "class": "sqli",
                            "target": "https://citadel.local",
                            "title": "login bypass",
                        },
                        "EXP-002": {
                            "id": "EXP-002",
                            "status": "FAILED_REPRO",
                            "class": "xss",
                            "target": "https://citadel.local",
                            "title": "stored",
                        },
                        "EXP-003": {
                            "id": "EXP-003",
                            "status": "CONFIRMED",
                            "class": "ssti",
                            "target": "https://other.example",
                            "title": "admin tpl",
                        },
                    }
                }
            )
        )
        monkeypatch.setattr(am, "_catalog_root", lambda: tmp_path / "exploits")
        lines = am.what_worked("http://citadel.local/login")
        assert any("sqli" in line for line in lines)
        assert not any("FAILED" in line or "xss" in line for line in lines[:3])
        assert any("Class transfer" in line and "ssti" in line for line in lines)

    def test_what_worked_empty_is_empty(self, tmp_path, monkeypatch):
        from suijin.modules.agent.lib import attack_memory as am

        monkeypatch.setattr(am, "_catalog_root", lambda: tmp_path)
        assert am.what_worked("nothing") == []


class TestChainPlanner:
    def test_creds_plus_login_chain(self):
        from suijin.modules.agent.lib.attack_memory import plan_chains

        st = _st(target_info={"credentials": [{"k": "eyjwtok"}], "endpoints": ["/login", "/api"]})
        chains = plan_chains(st)
        assert any("replay" in c for c in chains)

    def test_upload_chain(self):
        from suijin.modules.agent.lib.attack_memory import plan_chains

        st = _st(target_info={"credentials": [], "endpoints": ["/uploads", "/files"]})
        assert any("upload" in c.lower() for c in plan_chains(st))

    def test_foothold_chain(self):
        from suijin.modules.agent.lib.attack_memory import plan_chains

        assert any("privesc" in c for c in plan_chains(_st(_foothold_at=True)))


class TestPayloadMutate:
    def test_sql_variants(self):
        from suijin.modules.tools.lib.payload_mutate import payload_mutate

        out = payload_mutate("' OR 1=1--", blocked_response="403 forbidden")
        assert "case-rotation" in out and "inline comments" in out
        assert "family escalation" in out

    def test_class_detection(self):
        from suijin.modules.tools.lib.payload_mutate import detect_class

        assert detect_class("' union select 1") == "sql"
        assert detect_class("<img src=x onerror=alert(1)>") == "xss"
        assert detect_class("{{7*7}}") == "ssti"

    def test_empty_is_clean_error(self):
        from suijin.modules.tools.lib.payload_mutate import payload_mutate

        assert payload_mutate("").startswith("Error:")

    def test_dispatch_route(self):
        from suijin.modules.tools.lib.dispatch import route_tool

        out = route_tool("payload_mutate", {"payload": "' OR 1=1--"}, {})
        assert "variants" in str(out)


class TestDeadPathsRevived:
    """Wave 0 (session memory): the what_worked dict-fix, the supervisor/
    oracle LLM generate-fn contract, findings written on CONFIRMED, and
    the cross-engagement recall reaching think context."""

    def test_what_worked_tolerates_legacy_list_shape(self, tmp_path, monkeypatch):
        """Older catalogs (and the old fixture) wrote entries as a LIST —
        the reader now tolerates both shapes."""
        from suijin.modules.agent.lib import attack_memory as am

        eng = tmp_path / "exploits" / "legacy"
        eng.mkdir(parents=True)
        (eng / "catalog.json").write_text(
            json.dumps({"entries": [{"id": "EXP-001", "status": "CONFIRMED", "class": "rce", "target": "http://t", "title": "x"}]})
        )
        monkeypatch.setattr(am, "_catalog_root", lambda: tmp_path / "exploits")
        lines = am.what_worked("http://t")
        assert any("rce" in ln for ln in lines)

    def test_supervisor_llm_call_matches_generate_contract(self):
        """The every-15th-iter deep analysis was DEAD: prompt=/system=
        kwargs mismatched the (messages, max_tokens=…) seam and the
        TypeError was swallowed. It must now go through as messages."""
        import asyncio

        from suijin.modules.agent.lib.supervisor import analyze_trace_with_llm

        calls = []

        async def fake_generate(messages, config=None, on_delta=None, **kw):
            calls.append((messages, kw))
            return "focus on the upload endpoint"

        trace = [{"tool_name": "http_request", "thought": "probing", "success": True}]
        out = asyncio.run(analyze_trace_with_llm(trace, {"current_phase": "recon"}, fake_generate))
        assert out == "focus on the upload endpoint"
        assert calls and isinstance(calls[0][0], list) and calls[0][0][0]["role"] == "system"
        assert calls[0][1].get("max_tokens") == 150

    def test_supervisor_llm_dead_contract_still_never_raises(self):
        import asyncio

        from suijin.modules.agent.lib.supervisor import analyze_trace_with_llm

        async def broken_generate(messages, **kw):
            raise RuntimeError("provider down")

        assert asyncio.run(analyze_trace_with_llm([{"tool_name": "x"}], {}, broken_generate)) is None

    def test_oracle_llm_hypotheses_now_reach_the_model(self):
        import asyncio

        from suijin.modules.redteam.lib.intel.oracle import generate_hypotheses_async

        async def fake_generate(messages, config=None, on_delta=None, **kw):
            return '[{"id": "H1", "hypothesis": "verbose error oracle", "validation_payload": "?id=1 AND SLEEP(5)"}]'

        hyps = asyncio.run(generate_hypotheses_async("MySQL syntax error near", {}, fake_generate))
        assert hyps and hyps[0]["id"] == "H1"
