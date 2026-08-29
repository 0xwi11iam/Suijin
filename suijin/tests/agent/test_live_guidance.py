"""Live guidance — the file-based operator->AI channel."""

import json

import pytest

import suijin.modules.platform.lib.workspace as ws
from suijin.modules.agent.lib import live_guidance as lg


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    ws._CURRENT_ENGAGEMENT = None
    ws.set_engagement("live guidance test")
    yield tmp_path
    ws._CURRENT_ENGAGEMENT = None


class TestGuidanceFile:
    def test_write_then_read_and_clear(self):
        lg.write_guidance("focus on the admin panel", mode="exploit")
        lg.write_guidance("also check /api/v2", mode="recon")
        body = lg.read_and_clear_guidance()
        assert "focus on the admin panel" in body
        assert "[EXPLOIT]" in body and "[RECON]" in body
        assert lg.read_and_clear_guidance() == ""

    def test_empty_when_no_file(self):
        assert lg.read_and_clear_guidance() == ""

    captured = {}

    def test_guidance_at_top_of_system_prompt(self):
        import asyncio

        from suijin.modules.agent.lib.nodes.think_node import think_node

        lg.write_guidance("priority: test /graphql")

        async def gen(messages, config=None, **kw):
            self.captured["sys"] = messages[0]["content"]
            return json.dumps({"action": "complete", "completion_reason": "done", "thought": "t"})

        state = {"objective": "t", "original_objective": "test target", "messages": [], "target_info": {}}
        asyncio.run(think_node(state, generate_fn=gen, config={}))
        sys_prompt = self.captured["sys"]
        assert "OPERATOR GUIDANCE" in sys_prompt
        assert "priority: test /graphql" in sys_prompt
        doctrine_idx = sys_prompt.find("PROFESSIONAL ENGAGEMENT")
        guidance_idx = sys_prompt.find("OPERATOR GUIDANCE")
        if doctrine_idx != -1:
            assert guidance_idx < doctrine_idx

    def test_no_guidance_no_block(self):
        import asyncio

        from suijin.modules.agent.lib.nodes.think_node import think_node

        async def gen(messages, config=None, **kw):
            self.captured["plain"] = messages[0]["content"]
            return json.dumps({"action": "complete", "completion_reason": "done", "thought": "t"})

        state = {"objective": "t", "original_objective": "test target", "messages": [], "target_info": {}}
        asyncio.run(think_node(state, generate_fn=gen, config={}))
        assert "OPERATOR GUIDANCE" not in self.captured["plain"]


class TestContextManifest:
    def test_manifest_written(self):
        lg.write_context_manifest(
            guidance="test the search",
            phase="recon",
            iteration=1,
            attack_path="recon",
            recent_actions="nmap",
            msg_count=3,
            prompt_chars=5000,
        )
        body = lg.context_path().read_text()
        assert "test the search" in body and "iteration: 1" in body

    def test_manifest_overwrites(self):
        lg.write_context_manifest(
            guidance="first", phase="a", iteration=1, attack_path="x", recent_actions="", msg_count=1, prompt_chars=100
        )
        lg.write_context_manifest(
            guidance="second", phase="b", iteration=2, attack_path="y", recent_actions="", msg_count=2, prompt_chars=200
        )
        body = lg.context_path().read_text()
        assert "second" in body and "first" not in body
