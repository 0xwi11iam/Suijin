"""The context-delivery + chaining build (R1–R8), every refinement traced
to the field complaints: raw results burning every turn's embed, compaction
eating the crown jewels, scavenging instead of chaining, and a supervisor
that talked too much.

All hosts are example.com (AGENTS.md).
"""

from __future__ import annotations


class TestR1Distillation:
    def test_big_result_is_capped_with_a_pointer(self):
        from suijin.modules.agent.lib.context_distill import distill_result

        huge = "Status: 200\nServer: nginx\n\n" + ("A" * 35_000)
        out = distill_result(huge, cap=8_000)
        assert len(out) < 9_000
        assert "Status: 200" in out and "Server: nginx" in out
        assert "full output lives in the audit trail" in out

    def test_small_result_passes_through_untouched(self):
        from suijin.modules.agent.lib.context_distill import distill_result

        small = "Status: 403\nBody:\nforbidden"
        assert distill_result(small) == small

    def test_config_cap_is_honored(self):
        from suijin.modules.agent.lib.context_distill import distill_result

        out = distill_result("X" * 20_000, cap=2_000)
        assert len(out) < 2_500

    def test_execute_result_message_is_distilled_and_pin_tagged(self):
        from suijin.modules.agent.lib.nodes.execute_tool_node import _state_result_message

        msg = _state_result_message(
            "http_request", 120, {"iteration": 3}, "Status: 200\n\n" + "B" * 40_000, {"_run_config": {}}
        )
        assert msg.startswith("RESULT (http_request, 120ms, iteration 3):")
        assert len(msg) < 9_500  # distilled, not raw
        assert "audit trail" in msg

        pinned = _state_result_message(
            "catalog_exploit", 90, {"iteration": 4}, "EXP-001 CONFIRMED — MEDIUM CVSS 5.3 : leak", {}
        )
        assert pinned.startswith("[PIN] RESULT")


class TestR2PinsThroughCompaction:
    def _msgs(self):
        return [
            {
                "role": "user",
                "content": "[PIN] RESULT (catalog_exploit): EXP-001 CONFIRMED — HIGH : auth bypass + evidence lines",
            },
            {
                "role": "assistant",
                "content": '{"action":"use_tool","tool_name":"http_request","thought":"probe /admin"}',
            },
        ] + [{"role": "user", "content": f"RESULT (nmap_scan, 10ms, iteration {i}):\nscan noise"} for i in range(1, 30)]

    def test_pins_survive_compaction_verbatim(self):
        from suijin.modules.agent.lib.compact import compact

        out = compact(self._msgs(), trigger_chars=200, keep_recent=4)
        # the pin block rides verbatim, directly above the kept window
        pinned = [m for m in out if "EXP-001 CONFIRMED" in str(m.get("content", ""))]
        assert pinned, "the pin was compacted away"
        assert "NEVER compressed" in pinned[0]["content"]
        assert "evidence lines" in pinned[0]["content"]  # verbatim, not header-only

    def test_digest_remembers_assistant_decisions(self):
        from suijin.modules.agent.lib.compact import _summarize_older

        msgs = [
            {
                "role": "assistant",
                "content": '{"action":"use_tool","tool_name":"sqlmap","thought":"login is the target"}',
            },
            {"role": "user", "content": "RESULT (sqlmap, 5ms, iteration 2):\nno injection"},
        ] * 10
        digest = _summarize_older(msgs)
        assert "your past decisions" in digest
        assert "sqlmap" in digest

    def test_operator_answers_are_pins(self):
        import inspect

        from suijin.modules.redteam.lib import redteamer

        src = inspect.getsource(redteamer)
        assert src.count("[PIN] OPERATOR ANSWER") >= 3


class TestR7Footholds:
    def test_extraction_from_confirmed(self):
        from suijin.modules.agent.lib.footholds import extract_footholds

        fh = extract_footholds("EXP-002 CONFIRMED — HIGH CVSS 7.5 : auth bypass on login", iteration=9)
        assert len(fh) == 1
        assert fh[0]["source"] == "EXP-002"
        assert "auth bypass" in fh[0]["capability"]
        assert fh[0]["status"] == "open"

    def test_arming_names_unlocks(self):
        import asyncio

        from suijin.modules.agent.lib.footholds import arm_foothold

        async def gen(messages, config=None, **kw):
            return "admin panel via the session\ninternal API with the map\nNONE-line-ignored"

        fh = {"capability": "EXP-1: x", "source": "EXP-1", "unlock_targets": [], "born_iter": 1, "status": "open"}
        targets = asyncio.run(arm_foothold(fh, "EXP-1 confirmed", gen))
        assert "admin panel" in targets[0]
        assert fh["status"] == "armed"

    def test_you_hold_renders_with_age_and_unnamed_flag(self):
        from suijin.modules.agent.lib.footholds import render_you_hold

        fh = [
            {
                "capability": "EXP-1: session token",
                "source": "EXP-1",
                "unlock_targets": ["admin panel"],
                "born_iter": 2,
                "status": "armed",
            }
        ]
        out = render_you_hold(fh, iteration=7)
        assert "YOU HOLD" in out and "EXP-1" in out and "UNTESTED UNLOCK: admin panel" in out
        assert "5 turns old" in out

    def test_unexploited_and_closing(self):
        from suijin.modules.agent.lib.footholds import close_from_todo, unexploited

        fh = [
            {"capability": "a", "source": "EXP-1", "unlock_targets": ["x"], "born_iter": 1, "status": "armed"},
            {"capability": "b", "source": "EXP-2", "unlock_targets": [], "born_iter": 1, "status": "tested"},
        ]
        assert len(unexploited(fh)) == 1  # tested one excluded
        closed = close_from_todo(fh, "fh-EXP-1-0")
        assert all(f["status"] == "tested" for f in closed)

    def test_depth_gate_refuses_with_unexploited_unlocks(self):
        import inspect

        from suijin.modules.agent.lib.nodes import think_node

        src = inspect.getsource(think_node)
        assert "depth gate" in src and "weapons on the table" in src

    def test_you_hold_rides_the_phase_order(self):
        import inspect

        from suijin.modules.agent.lib.nodes import think_node

        src = inspect.getsource(think_node)
        exp_block = src[src.index('"exploitation":') : src.index("}", src.index('"exploitation":'))]
        post_block = src[src.index('"post_exploitation":') : src.index("}", src.index('"post_exploitation":'))]
        assert exp_block.index("YOU_HOLD") < exp_block.index("RECENT_ACTIONS")  # holdings lead in exploitation
        assert post_block.index("YOU_HOLD") == post_block.index("YOU_HOLD") and post_block.index(
            '"YOU_HOLD"'
        ) < post_block.index("TODO_LIST")


class TestR3R5R6ContextShape:
    def test_pins_ride_the_embed_first(self):
        import inspect

        from suijin.modules.agent.lib.nodes import think_node

        src = inspect.getsource(think_node)
        assert "[PINNED — always in context]" in src

    def test_guidance_joins_the_ttl(self):
        import inspect

        from suijin.modules.agent.lib.nodes import think_node

        assert '"OPERATOR GUIDANCE"' in inspect.getsource(think_node)

    def test_manifest_carries_section_sizes(self, tmp_path, monkeypatch):
        from suijin.modules.agent.lib import live_guidance as lg

        monkeypatch.setattr(lg, "context_path", lambda: tmp_path / "ctx.md")
        lg.write_context_manifest(
            guidance="",
            phase="exploitation",
            iteration=3,
            attack_path="recon",
            recent_actions="",
            msg_count=10,
            prompt_chars=20_000,
            section_sizes={"YOU_HOLD": 400, "RULES": 1200},
        )
        text = (tmp_path / "ctx.md").read_text()
        assert "CONTEXT SECTIONS" in text and "YOU_HOLD: 400 chars" in text


class TestR8SupervisorHelpfulNotBullshit:
    def test_intervention_budget_one_per_three_turns(self):
        from suijin.modules.agent.lib import supervisor as sup

        sup._INTERVENTION_STATE.clear()
        sup._last_fired.clear()
        trace = [{"tool_name": "ffuf", "success": True}] * 3  # rut → fires
        first = sup.analyze_trace(trace, iteration=10)
        assert first is not None
        # within the budget window: SILENT even for a fresh rut
        sup._INTERVENTION_STATE["last_turn"] = 10.0
        assert sup.analyze_trace(trace, iteration=12) is None
        # window elapsed + per-detector cooldown reset: speaks again
        sup._last_fired.clear()
        assert sup.analyze_trace(trace, iteration=14) is not None
        sup._INTERVENTION_STATE.clear()

    def test_tone_is_opportunity_not_accusation(self):
        from suijin.modules.agent.lib import supervisor as sup

        trace = [{"tool_name": "http_request", "success": True}] * 3
        sup._INTERVENTION_STATE.clear()
        msg = sup.analyze_trace(trace, iteration=99)
        assert msg is not None and msg.startswith("OPPORTUNITY:")
        assert "STOP" not in msg
        sup._INTERVENTION_STATE.clear()
