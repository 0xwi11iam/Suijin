"""The Librarian — engagement memory that actually recalls.

Contract (operator): interesting things found early surface the moment
they matter. Pattern-first extraction (zero LLM), a rare condensed
digest, every-turn relevant recall in the think context, on-demand
memory_recall tool, and the ledger riding the .sje bundle.
"""

import asyncio
import json

import pytest

import suijin.modules.agent.lib.librarian as lb


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    lb.stop()
    monkeypatch.setattr(lb, "_ACTIVE", None)
    yield
    lb.stop()


def _fake_gen(reply="digest line one\n- kept credential verbatim"):
    calls = []

    async def gen(messages, config=None, **kw):
        calls.append((messages, config))
        return reply

    gen.calls = calls
    return gen


def _start(tmp_path, gen=None, interval=10):
    return lb.start(generate_fn=gen, engagement_dir=tmp_path, interval=interval, target="http://t.local")


def _observe_wait(tool="http_request", args=None, output="", iteration=1):
    lb.observe(tool, args or {}, output, iteration)


class TestExtraction:
    def test_creds_leaks_footholds_confirmed(self, tmp_path):
        _start(tmp_path)
        _observe_wait(
            output=(
                "200 OK\n"
                "token=AKIAIOSFODNN7EXAMPLE and sk-live-abcdefghijklmnopqrst\n"
                "uid=0(root) via the ping filter\n"
                "EXP-001 CONFIRMED — CRITICAL CVSS 9.1 : ping filter RCE\n"
                "Server: nginx/1.18.0\n"
                "SQL syntax error near 'OR 1=1'\n"
                "https://t.local/admin/panel exists"
            ),
            args={"url": "https://t.local/x"},
        )
        lb.stop()  # flush
        led = json.loads((tmp_path / "librarian.json").read_text())
        kinds = {e["kind"] for e in led["entries"]}
        assert {"credential", "foothold", "confirmed exploit", "version", "error oracle", "admin surface"} <= kinds

    def test_dedup_same_value(self, tmp_path):
        _start(tmp_path)
        for _ in range(3):
            _observe_wait(output="AKIAIOSFODNN7EXAMPLE found again")
        lb.stop()
        led = json.loads((tmp_path / "librarian.json").read_text())
        vals = [e["value"] for e in led["entries"] if e["kind"] == "credential"]
        assert len(vals) == 1

    def test_ledger_survives_restart(self, tmp_path):
        _start(tmp_path)
        _observe_wait(output="ghp_" + "a" * 36)
        lb.stop()
        lb2 = _start(tmp_path)  # fresh process-run, same engagement dir
        assert lb2 is not None
        assert any(e["kind"] == "credential" for e in lb2._ledger["entries"])

    def test_kind_cap(self, tmp_path):
        _start(tmp_path)
        for i in range(60):
            _observe_wait(output=f"uid={i}(root) shell {i}")
        lb.stop()
        led = json.loads((tmp_path / "librarian.json").read_text())
        assert sum(1 for e in led["entries"] if e["kind"] == "foothold") <= lb._KIND_CAP


class TestRecall:
    def test_relevant_matches_current_target(self, tmp_path):
        import time as _t

        _start(tmp_path)
        _observe_wait(output="AKIAIOSFODNN7EXAMPLE leaked", args={"url": "https://vault.t.local/"})
        _observe_wait(output="nginx/1.18.0 on the edge", args={"url": "https://edge.other.host/"})
        _t.sleep(0.7)  # drain into the ledger before recall
        hits = lb.relevant_for_step(
            {"original_objective": "pentest http://t.local"}, {"url": "https://vault.t.local/secret"}
        )
        assert any("AKIAIOSFODNN7EXAMPLE" in h for h in hits)
        assert not any("nginx" in h for h in hits)  # the edge observation is not relevant here

    def test_relevant_empty_without_librarian(self):
        assert lb.relevant_for_step({}, {}) == []

    def test_memory_recall_tool_body(self, tmp_path):
        import time as _t

        _start(tmp_path)
        _observe_wait(output="EXP-001 CONFIRMED — HIGH : sqli in /search")
        _t.sleep(0.7)  # let the thread drain the queue into the ledger
        out = lb.recall(query="confirmed", limit=5)
        lb.stop()
        assert "EXP-001" in out

    def test_recall_without_librarian_is_honest(self):
        out = lb.recall()
        assert "librarian" in out.lower()


class TestDigest:
    def test_digest_fires_on_interval(self, tmp_path):
        gen = _fake_gen()
        _start(tmp_path, gen=gen, interval=3)
        for _ in range(3):
            _observe_wait(output="AKIAIOSFODNN7EXAMPLE x")
        import time as _t

        _t.sleep(1.2)  # let the thread's mid-run drain fire (0.5s cadence)
        lb.stop()
        assert len(gen.calls) == 1
        led = json.loads((tmp_path / "librarian.json").read_text())
        assert led["digests"] and "kept credential verbatim" in led["digests"][0]["lines"]

    def test_no_digest_when_gen_fails(self, tmp_path):
        async def broken(messages, config=None, **kw):
            raise RuntimeError("provider down")

        _start(tmp_path, gen=broken, interval=1)
        _observe_wait(output="AKIAIOSFODNN7EXAMPLE x")
        lb.stop()  # must not raise
        led = json.loads((tmp_path / "librarian.json").read_text())
        assert led["entries"]  # extraction survived the dead LLM

    def test_final_stop_never_calls_the_llm(self, tmp_path):
        gen = _fake_gen()
        _start(tmp_path, gen=gen, interval=100_000)  # never mid-run
        _observe_wait(output="AKIAIOSFODNN7EXAMPLE x")
        lb.stop()  # final flush — NO call at teardown
        assert gen.calls == []


class TestUiStatePublish:
    def test_count_published_via_hook(self, tmp_path, monkeypatch):
        import time as _t

        published = []
        monkeypatch.setattr(lb, "_UI_PUBLISH", lambda n: published.append(n))
        _start(tmp_path)
        _observe_wait(output="AKIAIOSFODNN7EXAMPLE x")
        _t.sleep(0.7)
        lb.stop()
        assert published and published[-1] >= 1  # the strip count went live


class TestObserveTap:
    def test_observe_never_raises_without_librarian(self):
        lb.observe("http_request", {}, "whatever", 1)  # no active — silent

    def test_observe_queue_overflow_drops(self, tmp_path):
        _start(tmp_path)
        for i in range(3000):  # past the 2000 queue cap
            lb.observe("t", {}, f"x {i}", 1)
        lb.stop()  # must not raise


class TestIntegration:
    def test_think_context_renders_the_recall(self, tmp_path):
        """The top-of-mind contract: the LIBRARIAN block appears in the
        system prompt exactly when the current step touches a remembered
        target."""
        import time as _t

        from suijin.modules.agent.lib.nodes.think_node import think_node

        _start(tmp_path)
        _observe_wait(output="AKIAIOSFODNN7EXAMPLE leaked", args={"url": "https://vault.t.local/"})
        _t.sleep(0.7)

        async def gen(messages, config=None, **kw):
            seen.append(messages[0]["content"])
            return '{"action":"use_tool","tool_name":"search_kb","tool_args":{"keyword":"x"},"thought":"t"}'

        seen = []
        st = {
            "objective": "o",
            "original_objective": "pentest http://t.local",
            "target_info": {},
            "messages": [],
            "current_iteration": 1,
            "_current_step": {"tool_args": {"url": "https://vault.t.local/secret"}},
        }
        asyncio.run(think_node(st, generate_fn=gen, config={}))
        lb.stop()
        prompt = seen[0]
        assert "LIBRARIAN" in prompt and "AKIAIOSFODNN7EXAMPLE" in prompt

    def test_memory_recall_routes_through_dispatch(self, tmp_path):
        import time as _t

        from suijin.modules.tools.lib.dispatch import route_tool

        _start(tmp_path)
        _observe_wait(output="EXP-002 CONFIRMED — CRITICAL : ssrf to metadata", args={"url": "http://t.local/proxy"})
        _t.sleep(0.7)
        out = str(route_tool("memory_recall", {"query": "confirmed"}, {}))
        lb.stop()
        assert "EXP-002" in out and not out.startswith("Error:")

    def test_catalog_lists_memory_recall(self):
        from suijin.modules.loader import discover_modules
        from suijin.modules.tools.lib.dispatch import get_tool_catalog

        discover_modules()
        assert "memory_recall" in get_tool_catalog()

    def test_ledger_rides_the_sje_bundle(self, tmp_path, monkeypatch):
        import suijin.modules.platform.lib.workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        from suijin.modules.tools.lib import engagement_bundle as eb

        (ws.WORKSPACE_DIR / "outputs" / "exports").mkdir(parents=True, exist_ok=True)
        ws.set_engagement("obj http://t.local")  # the bundle reads the LIVE engagement dir
        edir = ws.engagement_dir()
        edir.mkdir(parents=True, exist_ok=True)
        (edir / "librarian.json").write_text(
            json.dumps({"entries": [{"kind": "credential", "value": "AKIAX", "where": "t", "iter": 2}]})
        )
        monkeypatch.setattr(eb, "_engagement_slug", lambda o: "x")
        path = eb.save_engagement("t1", "obj http://t.local", {}, {"messages": [], "current_iteration": 3})
        assert path.is_file()
        bundle = eb.load_engagement(path)
        led = bundle["graph_state"].get("_librarian_ledger")
        assert led and led["entries"][0]["value"] == "AKIAX"
