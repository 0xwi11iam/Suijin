"""Kernel events bus + Context — tests."""

import threading

from suijin.kernel.context import Context
from suijin.kernel.events import EventBus


class TestEventBus:
    def test_pubsub_roundtrip(self):
        bus = EventBus()
        got = []
        bus.on("tool.after", lambda payload: got.append(payload))
        bus.emit("tool.after", {"tool": "nmap"})
        assert got == [{"tool": "nmap"}]

    def test_multiple_subscribers(self):
        bus = EventBus()
        a, b = [], []
        bus.on("x", lambda p: a.append(1))
        bus.on("x", lambda p: b.append(1))
        bus.emit("x", None)
        assert a and b

    def test_subscriber_error_isolated(self):
        """A broken listener must not kill the chain — core kernel rule."""
        bus = EventBus()
        ok = []
        bus.on("x", lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
        bus.on("x", lambda p: ok.append("still-ran"))
        bus.emit("x", None)  # must not raise
        assert ok == ["still-ran"]

    def test_off_unsubscribes(self):
        bus = EventBus()
        got = []

        def listener(p):
            got.append(p)

        bus.on("x", listener)
        bus.off("x", listener)
        bus.emit("x", 1)
        assert got == []

    def test_emit_unknown_event_noop(self):
        EventBus().emit("never-subscribed", {"a": 1})  # must not raise

    def test_thread_safe(self):
        bus = EventBus()
        got = []
        bus.on("tick", lambda p: got.append(p))
        threads = [threading.Thread(target=lambda i=i: bus.emit("tick", i)) for i in range(20)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(got) == list(range(20))


class TestContext:
    def _ctx(self) -> Context:
        return Context(config={"a": 1}, workspace="/tmp/x")

    def test_services_register_get(self):
        ctx = self._ctx()
        ctx.register_service("scorer", lambda: "S")
        assert ctx.service("scorer") == "S"

    def test_service_missing_returns_none(self):
        assert self._ctx().service("nope") is None

    def test_services_lazy_materialize_once(self):
        ctx = self._ctx()
        calls = []
        ctx.register_service("lazy", lambda: calls.append(1) or "v")
        assert calls == []
        assert ctx.service("lazy") == "v"
        ctx.service("lazy")
        assert calls == [1]  # produced exactly once

    def test_tools_register_and_route(self):
        ctx = self._ctx()

        def tool(args, c):
            return f"ran {args['x']}"

        ctx.register_tool("demo.scan", tool, description="demo")
        assert ctx.has_tool("demo.scan")
        assert ctx.call_tool("demo.scan", {"x": 1}) == "ran 1"

    def test_call_unknown_tool(self):
        assert self._ctx().call_tool("ghost", {}) == "Error: unknown tool 'ghost'"

    def test_tool_exception_contained(self):
        ctx = self._ctx()

        def boom(args, c):
            raise RuntimeError("kaboom")

        ctx.register_tool("demo.boom", boom, description="b")
        out = ctx.call_tool("demo.boom", {})
        assert "Error" in out and "kaboom" in out

    def test_events_reachable(self):
        ctx = self._ctx()
        got = []
        ctx.events.on("e", lambda p: got.append(p))
        ctx.events.emit("e", 42)
        assert got == [42]

    def test_events_on_ctx_register(self):
        ctx = self._ctx()
        got = []
        ctx.on_event("e", lambda p: got.append(p))
        ctx.events.emit("e", 1)
        assert got == [1]
