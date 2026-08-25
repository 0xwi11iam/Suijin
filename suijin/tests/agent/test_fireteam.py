"""Fireteam usefulness gates — a wasted specialist is worse than none.

Proves: vague/duplicate tasks rejected with actionable reasons; results
compressed to evidence (low-evidence tagged); deploy is non-blocking;
results drain on later turns; status nudges the agent back to work.
"""

import asyncio
import json

from suijin.modules.agent.lib.nodes import subagent_node as sn


def _gen_script(responses):
    """generate_fn replaying a script; records messages seen."""
    seen = []

    async def gen(messages, config=None, **kw):
        seen.append(list(messages))
        if isinstance(responses, list):
            if not responses:
                raise RuntimeError("script exhausted")
            return responses.pop(0)
        return responses

    gen.seen = seen
    return gen


class TestTaskGate:
    def setup_method(self):
        sn._reset_fireteams()

    def test_vague_rejected(self):
        # gate contract: <15 chars or <3 words = too vague to act on
        assert sn._gate_task("scan") is not None  # 4 chars
        assert sn._gate_task("x") is not None
        assert sn._gate_task("check web") is not None  # 2 words

    def test_specific_accepted(self):
        assert sn._gate_task("Test SQLi on http://target/login via time-based blind") is None

    def test_duplicate_rejected(self):
        t = "Test SQLi on http://target/login via time-based blind"
        assert sn._gate_task(t) is None
        sn._RECENT_TASKS[sn._norm_task(t)] = __import__("time").monotonic()
        reason = sn._gate_task(t)
        assert reason and "duplicate" in reason

    def test_deploy_all_rejected_returns_none_team(self):
        async def go():
            return sn.deploy_fireteam(
                ["scan", "x", "check web"],
                generate_fn=_gen_script("x"),
                route_tool_fn=lambda *a: "ok",
            )

        dep = asyncio.run(go())
        assert dep["team_id"] is None and dep["skipped"]
        assert "no specialists deployed" in dep["note"]

    def test_deploy_partial_spawn_and_skip(self):
        async def go():
            return sn.deploy_fireteam(
                ["Test SSTI on http://t/profile with {{7*7}} probe", "scan"],
                generate_fn=_gen_script("x"),
                route_tool_fn=lambda *a: "ok",
            )

        dep = asyncio.run(go())
        assert dep["team_id"] and len(dep["spawned"]) == 1
        assert len(dep["skipped"]) == 1 and "vague" in dep["skipped"][0][1]


class TestFindingsCompression:
    def test_evidence_first_low_flag(self):
        parts = [
            "[COMPLETE] found reflected XSS",
            "[http_request] 200 OK welcome page",
            "[http_request] <script>alert(1)</script> reflected in /search — XSS confirmed",
            "[read_file] blah blah filler line with nothing",
            "[http_request] 403 blocked on /admin — auth required",
        ]
        out, low = sn._compress_findings(parts)
        assert low is False
        assert "[COMPLETE]" in out and "XSS confirmed" in out
        assert "filler" not in out  # noise dropped

    def test_low_evidence_flagged(self):
        parts = ["[COMPLETE] could not test", "[read_file] some plain output line"]
        out, low = sn._compress_findings(parts)
        assert low is True and "[COMPLETE]" in out

    def test_run_subagent_tags_low_evidence(self):
        script = [
            json.dumps(
                {"action": "use_tool", "tool_name": "read_file", "tool_args": {"file_path": "/tmp/x"}, "thought": "t"}
            ),
            json.dumps({"action": "complete", "completion_reason": "nothing found", "thought": "t"}),
        ]

        async def go():
            return await sn.run_subagent(
                "Check http://target.example/robots.txt for hidden paths and report",
                generate_fn=_gen_script(script),
                route_tool_fn=lambda *a, **k: "plain text no markers",
            )

        r = asyncio.run(go())
        assert r.success and r.findings.startswith("(LOW EVIDENCE")


class TestNonBlockingAndDrain:
    def setup_method(self):
        sn._reset_fireteams()

    def test_deploy_then_drain_on_later_turn(self):
        script = [
            json.dumps(
                {"action": "use_tool", "tool_name": "http_request", "tool_args": {"url": "http://t/x"}, "thought": "t"}
            ),
            json.dumps({"action": "complete", "completion_reason": "200 OK confirmed reachable", "thought": "t"}),
        ]

        async def go():
            dep = sn.deploy_fireteam(
                ["Probe http://t/x reachability and confirm status code"],
                generate_fn=_gen_script(script),
                route_tool_fn=lambda name, args, cfg: "HTTP 200 OK — server up",
            )
            assert dep["team_id"], dep
            # deploy returned instantly — team still running
            assert sn.fireteam_status().startswith("No change") is False or "running" in sn.fireteam_status()
            await asyncio.sleep(0.2)  # let the specialist finish
            msgs = sn.collect_finished_teams()
            return msgs

        msgs = asyncio.run(go())
        assert msgs and "FIRETEAM RESULT" in msgs[0] and "200 OK confirmed" in msgs[0]

    def test_status_nudge_when_unchanged(self):
        sn._LAST_STATUS_SIG = ("team-x", 1, 0)
        sn._FIRETEAMS["team-x"] = {"tasks": ["t"], "futures": [], "started": "", "results": []}
        # signature matches _LAST_STATUS_SIG (1 futures? no — 0 futures, 0 results) -> set matching sig
        sn._LAST_STATUS_SIG = (("team-x", 0, 0),)
        out = sn.fireteam_status()
        assert "No change since your last check" in out
        assert "keep working" in out


class TestThinkIntegration:
    def test_deploy_via_think_is_instant_and_reports(self):
        from suijin.modules.agent.lib.nodes.think_node import think_node

        deploy = json.dumps(
            {
                "action": "deploy_subagent",
                "thought": "parallel probes",
                "subagent_task": "scan || Test SSTI on http://t/profile with {{7*7}} injections",
            }
        )

        async def gen(messages, config=None, **kw):
            return deploy

        async def go():
            st = {"objective": "o", "target_info": {}, "messages": [], "current_iteration": 1}
            out = await think_node(st, generate_fn=gen, config={})
            await asyncio.sleep(0.05)  # background team tick
            sn._reset_fireteams()
            return out

        out = asyncio.run(go())
        msgs = [m["content"] for m in out.get("messages", []) if "FIRETEAM" in m.get("content", "")]
        assert msgs, out.get("messages")
        assert "SKIPPED" in msgs[0] and "vague" in msgs[0]  # 'scan' rejected, SSTI kept
        assert "specialist(s) running" in msgs[0]


class TestStatusAndDrainHonesty:
    """The tweaky bits, fixed: per-task states from results (not `?` for
    everything), and FAILED results never labeled PARTIAL."""

    def setup_method(self):
        sn._reset_fireteams()

    def test_status_shows_real_task_states(self):
        import asyncio

        async def go():
            from suijin.modules.agent.lib.nodes.subagent_node import SubagentResult

            loop = asyncio.get_running_loop()
            pending = loop.create_future()  # agent B still flying
            done_a = SubagentResult(subagent_id="s1", task="task A text", success=True, findings="found", steps=2)
            sn._FIRETEAMS["team-x"] = {
                "tasks": ["task A text", "task B text"],
                "futures": [pending],
                "started": "",
                "results": [done_a],
            }
            return sn.fireteam_status()

        out = asyncio.run(go())
        assert "[done OK] task A text" in out  # finished while a sibling runs
        assert "[running] task B text" in out  # not `?`

    def test_status_failed_task_labeled_failed(self):
        from suijin.modules.agent.lib.nodes.subagent_node import SubagentResult

        r = SubagentResult(subagent_id="s1", task="bad task", success=False, partial=False, findings="", steps=1)
        sn._FIRETEAMS["team-y"] = {"tasks": ["bad task"], "futures": [], "started": "", "results": [r]}
        assert "[done FAILED] bad task" in sn.fireteam_status()

    def test_drain_labels_total_failure_FAILED(self):
        import asyncio

        from suijin.modules.agent.lib.nodes.subagent_node import SubagentResult

        async def go():
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            fut.set_result(
                SubagentResult(subagent_id="s1", task="t", success=False, partial=False, findings="nope", steps=1)
            )
            sn._FIRETEAMS["team-f"] = {"tasks": ["t"], "futures": [fut], "started": "", "results": []}
            return sn.collect_finished_teams()

        msgs = asyncio.run(go())
        assert msgs and "FAILED" in msgs[0]
        assert "PARTIAL" not in msgs[0]

    def test_drain_timeout_partial_labeled(self):
        import asyncio

        from suijin.modules.agent.lib.nodes.subagent_node import SubagentResult

        async def go():
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            fut.set_result(
                SubagentResult(subagent_id="s1", task="t", success=False, partial=True, findings="half", steps=9)
            )
            sn._FIRETEAMS["team-p"] = {"tasks": ["t"], "futures": [fut], "started": "", "results": []}
            return sn.collect_finished_teams()

        msgs = asyncio.run(go())
        assert msgs and "TIMEOUT" in msgs[0]
