"""Live guidance — the file-based operator->AI channel."""

import asyncio
import json

import pytest

import suijin.modules.platform.lib.workspace as ws
from suijin.modules.agent.lib import live_guidance as lg


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    ws._CURRENT_ENGAGEMENT = None
    ws.set_engagement("lg test")
    yield tmp_path
    ws._CURRENT_ENGAGEMENT = None


def _run_think(state_extra=None):
    from suijin.modules.agent.lib.nodes.think_node import think_node

    got = []

    async def gen(messages, config=None, **kw):
        got.append(list(messages))
        return json.dumps({"action": "complete", "completion_reason": "done", "thought": "t"})

    state = {"objective": "t", "original_objective": "test target", "messages": [], "target_info": {}}
    state.update(state_extra or {})
    asyncio.run(think_node(state, generate_fn=gen, config={}))
    return got[0] if got else []


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

    def test_guidance_is_last_user_message(self):
        """Guidance rides as the LAST user message — highest attention."""
        lg.write_guidance("priority: test /graphql")
        msgs = _run_think()
        assert len(msgs) >= 3  # [system, objective, guidance]
        last = msgs[-1]
        assert last["role"] == "user"
        assert "OPERATOR GUIDANCE" in last["content"]
        assert "priority: test /graphql" in last["content"]
        # the system prompt does NOT contain it (not diluted there)
        assert "OPERATOR GUIDANCE" not in msgs[0]["content"]

    def test_no_guidance_no_extra_message(self):
        msgs = _run_think()
        assert len(msgs) == 2  # [system, objective] — no guidance rider
        assert "OPERATOR GUIDANCE" not in msgs[-1]["content"]


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
