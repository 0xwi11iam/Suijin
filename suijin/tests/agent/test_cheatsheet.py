"""The Dynamic Cheatsheet — cross-engagement memory.

Storage (workspace-level persistence, dedup, prune), the context
render, the agent tool seam, and conclusion distillation (mocked LLM).
The killer test: a dead battery stored by run one is IN run two's
context — the 22-token JWT battery never re-runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from suijin.modules.agent.lib import cheatsheet as cs  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    p = tmp_path / "cheatsheet.json"
    monkeypatch.setattr(cs, "_path", lambda: p)
    cs.reset_cache()
    yield p
    cs.reset_cache()


class TestStore:
    def test_add_and_read_back(self, store):
        assert "stored" in cs.add("JWT gate kid=primary: alg:none rejected (verifier falls back to HMAC)", tag="jwt")
        snips = cs.snippets()
        assert len(snips) == 1 and snips[0]["tag"] == "jwt"

    def test_dedup_on_normalized_text(self, store):
        cs.add("same   note", tag="a")
        assert "deduped" in cs.add("same note", tag="b")  # whitespace-normalized equal
        assert len(cs.snippets()) == 1

    def test_empty_rejected(self, store):
        assert cs.add("") == "Error: empty note"
        assert cs.add("   ") == "Error: empty note"

    def test_cap_prunes_oldest(self, store, monkeypatch):
        for i in range(cs.MAX_SNIPPETS + 5):
            cs.add(f"note {i}", tag="t")
        snips = cs.snippets()
        assert len(snips) == cs.MAX_SNIPPETS
        assert snips[0]["note"] == "note 5"  # the oldest five pruned

    def test_note_truncated(self, store):
        cs.add("x" * 1000, tag="t")
        assert len(cs.snippets()[0]["note"]) == cs.MAX_NOTE_CHARS

    def test_persistence_across_cache_resets(self, store):
        cs.add("persist me", tag="t")
        cs.reset_cache()
        assert any(s["note"] == "persist me" for s in cs.snippets())


class TestRender:
    def test_empty_renders_nothing(self, store):
        assert cs.render_for_context() == ""

    def test_serves_freshest_limited(self, store):
        for i in range(20):
            cs.add(f"note {i}", tag="t")
        out = cs.render_for_context()
        assert "note 19" in out and "note 8" in out  # freshest served
        assert "note 7" not in out  # beyond the serve limit
        assert out.count("\n") + 1 <= cs.SERVE_LIMIT


class TestDistillation:
    def test_parse_clean_json(self):
        out = cs.parse_distillation(
            'Some preamble [{"tag": "jwt", "note": "alg:none dead"}, {"tag": "recon", "note": "/certs leaks the PEM"}] trailing'
        )
        assert len(out) == 2 and out[0]["tag"] == "jwt"

    def test_parse_garbage_is_empty(self):
        assert cs.parse_distillation("no json here") == []
        assert cs.parse_distillation("[not json") == []

    def test_distill_stores_via_generate(self, store):
        calls = []

        def fake_gen(msgs, cfg):
            calls.append(msgs)
            return '[{"tag":"jwt","note":"22 secret battery dead: kid=primary, HS256-with-cert is the live lead"}]'

        n = cs.distill_from_trace("trace tail...", fake_gen)
        assert n == 1
        assert any("22 secret battery" in s["note"] for s in cs.snippets())
        assert "transferable" in calls[0][0]["content"].lower()  # the prompt demands transferability

    def test_distill_llm_failure_is_silent(self, store):
        def boom(msgs, cfg):
            raise RuntimeError("provider down")

        assert cs.distill_from_trace("tail", boom) == 0
        assert cs.snippets() == []


class TestTheKillerCase:
    def test_run_two_starts_from_run_ones_dead_battery(self, store):
        """The whole point: run one's failed JWT battery appears in run
        two's context — the next engagement skips the 22 calls."""
        # run one: the agent self-reports the dead battery
        from suijin.modules.tools.lib.dispatch import route_tool

        route_tool(
            "cheatsheet_note",
            {
                "note": "edge JWT gate kid=primary: alg:none + 21 weak secrets all rejected; /certs exposes the PEM — try HS256-keyed-with-PEM",
                "tag": "jwt",
            },
            {},
        )
        # conclusion distillation adds what the agent didn't report
        cs.distill_from_trace(
            "jwt alg none -> invalid token (x21)",
            lambda m, c: '[{"tag":"jwt","note":"verifier: HS256 fallback keyed by the public cert"}]',
        )
        # run two: the context section carries both
        ctx = cs.render_for_context()
        assert "HS256-keyed-with-PEM" in ctx
        assert "public cert" in ctx


class TestContextInjection:
    def test_think_context_actually_serves_the_cheatsheet(self, store, monkeypatch):
        """NOT source-grep: drive the REAL think_node with a stored
        snippet and a fake LLM; the assembled user turn must carry the
        CHEATSHEET section inside the untrusted boundary. If this passes,
        the cheatsheet is live wiring — anything less is dead code."""
        import asyncio

        from suijin.modules.agent.lib.nodes.think_node import think_node

        cs.add("JWT gate kid=primary: alg:none + weak secrets dead; try HS256-with-cert", tag="jwt")
        seen = []

        async def gen(messages, config=None, **kw):
            seen.append(messages)
            return '{"action":"use_tool","tool_name":"search_kb","tool_args":{"keyword":"x"},"thought":"t"}'

        st = {
            "objective": "o",
            "original_objective": "pentest http://t.local",
            "target_info": {},
            "messages": [],
            "current_iteration": 1,
        }
        asyncio.run(think_node(st, generate_fn=gen, config={}))
        # the context block rides the SYSTEM message (system_prompt + context_block)
        system_turn = next(m["content"] for m in seen[0] if m["role"] == "system")
        assert "CHEATSHEET" in system_turn, "section missing — dead code"
        assert "HS256-with-cert" in system_turn, "snippet not served"
        assert "UNTRUSTED" in system_turn.split("CHEATSHEET")[1][:200], (
            "not inside the untrusted boundary: " + system_turn[-400:]
        )

    def test_no_snippets_no_section(self, store, monkeypatch):
        import asyncio

        from suijin.modules.agent.lib.nodes.think_node import think_node

        seen = []

        async def gen(messages, config=None, **kw):
            seen.append(messages)
            return '{"action":"use_tool","tool_name":"search_kb","tool_args":{"keyword":"x"},"thought":"t"}'

        st = {
            "objective": "o",
            "original_objective": "pentest http://t.local",
            "target_info": {},
            "messages": [],
            "current_iteration": 1,
        }
        asyncio.run(think_node(st, generate_fn=gen, config={}))
        system_turn = next(m["content"] for m in seen[0] if m["role"] == "system")
        assert "CHEATSHEET" not in system_turn  # empty memory costs zero context
