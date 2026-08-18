"""Kernel jobs, errors, config — tests for the next Phase 1 slices."""

import time

from suijin.kernel.config import LayeredConfig
from suijin.kernel.errors import BootError, DependencyError, PermissionDenied, QuarantinedModule
from suijin.kernel.jobs import JobScheduler


class TestErrors:
    def test_taxonomy_hierarchy(self):
        for exc in (BootError, DependencyError, PermissionDenied, QuarantinedModule):
            assert issubclass(exc, Exception)

    def test_boot_error_carries_reason(self):
        e = BootError("core module missing: platform")
        assert "platform" in str(e)


class TestLayeredConfig:
    def test_layering_order(self):
        cfg = LayeredConfig()
        cfg.add_layer("module_defaults", {"temperature": 0.4, "provider": "zai"})
        cfg.add_layer("user", {"temperature": 0.1})
        cfg.add_layer("env", {"provider": "deepseek"})
        assert cfg["temperature"] == 0.1  # user over module defaults
        assert cfg["provider"] == "deepseek"  # env over user
        assert cfg.get("nonexistent") is None

    def test_immutable_snapshot(self):
        cfg = LayeredConfig()
        cfg.add_layer("base", {"a": 1})
        snap = cfg.snapshot()
        snap["a"] = 99
        assert cfg["a"] == 1  # layers can't be mutated via snapshot

    def test_empty_config(self):
        cfg = LayeredConfig()
        assert cfg.snapshot() == {}
        assert cfg.get("x", "fallback") == "fallback"


class TestJobScheduler:
    def test_spawn_and_complete(self):
        sched = JobScheduler()
        jid = sched.spawn("scan", {"target": "x"}, fn=lambda n, a, c: "done")
        deadline = time.time() + 5
        while time.time() < deadline and sched.get(jid)["status"] != "done":
            time.sleep(0.05)
        assert sched.get(jid)["status"] == "done"
        assert "done" in sched.output(jid)

    def test_failure_captured(self):
        sched = JobScheduler()

        def boom(n, a, c):
            raise RuntimeError("nope")

        jid = sched.spawn("t", {}, fn=boom)
        deadline = time.time() + 5
        while time.time() < deadline and sched.get(jid)["status"] != "failed":
            time.sleep(0.05)
        j = sched.get(jid)
        assert j["status"] == "failed" and "nope" in j["error"]

    def test_cancel(self):
        sched = JobScheduler()
        jid = sched.spawn("slow", {}, fn=lambda n, a, c: time.sleep(30))
        assert sched.cancel(jid) is True
        deadline = time.time() + 5
        while time.time() < deadline and sched.get(jid)["status"] != "cancelled":
            time.sleep(0.05)
        assert sched.get(jid)["status"] == "cancelled"

    def test_list_and_missing(self):
        sched = JobScheduler()
        assert sched.get("ghost") is None
        jid = sched.spawn("q", {}, fn=lambda n, a, c: "ok")
        assert any(j["id"] == jid for j in sched.list())
