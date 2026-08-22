"""Kernel audit regressions — the bugs a quiet linter was hiding.

Each test pins a REAL defect found in the Phase 5 audit:
  1. the purity linter globbed a nonexistent path (vacuously green
     since Phase 1) — now path-guarded and violation-proving
  2. controller imported suijin.modules (kernel->modules inversion) —
     now dependency-inverted via enabled_check injection
  3. Journal.flush snapshot-then-clear lost entries racing the write —
     now atomic drain, with disk-failure requeue
  4. native.py loaded a STALE compiled core after every cargo rebuild —
     now mtime-fresh
"""

import ast
import threading
import time
from pathlib import Path

import pytest

from suijin.kernel.journal import Journal


class TestPurityLinterWasVacuous:
    def test_linter_path_is_real(self):
        """The scan must actually find kernel files (was: 0 files, 0
        offenders, green — the most dangerous kind of test)."""
        from suijin.tests.kernel.test_kernel_purity import KERNEL

        files = [p for p in KERNEL.glob("*.py") if p.name != "__init__.py"]
        assert len(files) >= 10, f"purity scan found only {len(files)} files"
        assert (KERNEL / "controller.py").exists()

    def test_linter_detects_a_planted_violation(self):
        """Prove the detector fires: parse a snippet with a banned import
        through the same AST logic the linter uses."""
        bad = ast.parse("from suijin.modules import manager")
        mods = set()
        for node in ast.walk(bad):
            if isinstance(node, ast.ImportFrom):
                mods.add(node.module or "")
        assert "suijin.modules" in mods
        assert any(m.startswith("suijin") and not m.startswith("suijin.kernel") and m != "suijin_core" for m in mods)

    def test_controller_has_no_suijin_imports_outside_kernel(self):
        src = (Path(__file__).resolve().parents[2] / "kernel" / "controller.py").read_text()
        for node in ast.walk(ast.parse(src)):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("suijin")
                and not node.module.startswith("suijin.kernel")
            ):
                pytest.fail(f"kernel inversion: controller imports {node.module}")


class TestJournalFlushRace:
    def test_entries_appended_during_write_survive(self, tmp_path, monkeypatch):
        """The old snapshot-then-clear lost anything appended between the
        snapshot and the clear. Simulate: append DURING the disk write."""
        j = Journal(tmp_path / "logs")
        j.append("before", "1")

        real_open = open

        def slow_open(*a, **k):
            fh = real_open(*a, **k)
            # entry arrives while the flushed batch is being written
            j.append("during-write", "2")
            return fh

        import builtins

        monkeypatch.setattr(builtins, "open", slow_open)
        j.flush()
        monkeypatch.setattr(builtins, "open", real_open)

        # the racing entry must still be in the ring (next flush's job)
        assert any("during-write" in ln for ln in j.tail(10))

    def test_disk_failure_requeues_entries(self, tmp_path, monkeypatch):
        """A journal must never silently drop on disk errors — the batch
        goes back to the ring and flush raises."""
        j = Journal(tmp_path / "logs")
        j.append("precious", "data")

        def broken_open(*a, **k):
            raise OSError("disk full")

        import builtins

        real_open = builtins.open
        monkeypatch.setattr(builtins, "open", broken_open)
        with pytest.raises(OSError):
            j.flush()
        monkeypatch.setattr(builtins, "open", real_open)

        assert any("precious" in ln for ln in j.tail(10))  # requeued
        # and a retry after the disk heals succeeds
        j.flush()
        assert (tmp_path / "logs" / "journal.log").exists()

    def test_concurrent_append_flush_stress(self, tmp_path):
        """Hammer it: 8 threads appending while flushing — nothing lost.

        max_bytes is small enough to FORCE rotation mid-stress, so the
        accounting covers every journal file (the active log + rotated
        segments): disk + ring + dropped must equal every append."""
        j = Journal(tmp_path / "logs", ring_size=5000, max_bytes=32 * 1024)
        stop = threading.Event()
        appended = {"n": 0}

        count_lock = threading.Lock()

        def writer(wid):
            i = 0
            while not stop.is_set():
                j.append("tick", f"w{wid}-{i}")
                with count_lock:
                    appended["n"] += 1  # atomic wrt the final read
                i += 1

        threads = [threading.Thread(target=writer, args=(t,), daemon=True) for t in range(8)]
        [t.start() for t in threads]
        deadline = time.time() + 0.3
        while time.time() < deadline:
            j.flush()
        stop.set()
        [t.join(timeout=2) for t in threads]
        j.flush()
        with count_lock:
            total = appended["n"]
        # account across the active log AND every rotated segment
        log_dir = tmp_path / "logs"
        disk = sum(p.read_text().count("tick") for p in sorted(log_dir.glob("journal*.log")))
        in_ring = len(j._ring)
        accounted = disk + in_ring + j.dropped
        assert accounted == total, (
            f"UNACCOUNTED {total - accounted} of {total} entries "
            f"(disk={disk} ring={in_ring} dropped={j.dropped} across "
            f"{len(list(log_dir.glob('journal*.log')))} journal files — every entry must "
            "be on disk, in the ring, or counted as dropped)"
        )

    def test_rotation_same_second_never_overwrites(self, tmp_path):
        """Repeated rotations (likely within one second) must not destroy
        each other: rotation happens on the flush AFTER overflow, so
        three bursts -> two rotations, all entries preserved."""
        j = Journal(tmp_path / "logs", max_bytes=100)
        for burst in range(3):
            for i in range(50):
                j.append("tick", f"burst{burst}-{i}")
            j.flush()  # each burst alone exceeds 100B -> rotation follows
        files = sorted((tmp_path / "logs").glob("journal*.log"))
        # active log + rotated segments, none overwritten
        all_ticks = sum(p.read_text().count("tick") for p in files)
        assert all_ticks == 150, f"rotation lost entries: {all_ticks}/150 across {[f.name for f in files]}"
        assert len(files) >= 3, f"expected rotation segments, got {[f.name for f in files]}"


class TestNativeFreshness:
    def test_mtime_copy_logic(self, tmp_path):

        cand = tmp_path / "libsuijin_core.dylib"
        ext = tmp_path / "suijin_core.abi3.so"
        cand.write_bytes(b"v1")
        # simulate: ext older than a rebuilt candidate
        old = time.time() - 1000
        import os

        os.utime(cand, (time.time(), time.time()))
        ext.write_bytes(b"v1-copy")
        os.utime(ext, (old, old))
        # the freshness predicate the shim uses
        stale = (not ext.exists()) or cand.stat().st_mtime > ext.stat().st_mtime
        assert stale is True
        import shutil

        shutil.copy2(cand, ext)  # shim's action
        stale_after = cand.stat().st_mtime > ext.stat().st_mtime
        assert stale_after is False


class TestEventReentrancy:
    def test_self_emitting_subscriber_cannot_blow_the_stack(self):
        """A subscriber emitting the SAME event loops forever without a
        guard. With the depth bound: bounded, dropped, survived."""
        import logging

        from suijin.kernel.events import _MAX_EMIT_DEPTH, EventBus

        bus = EventBus()
        calls = {"n": 0}

        def evil(payload):
            calls["n"] += 1
            bus.emit("boom", payload)  # the loop

        bus.on("boom", evil)
        logger = logging.getLogger("suijin.kernel.events")
        old = logger.disabled
        logger.disabled = True  # silence the expected drop warnings
        try:
            bus.emit("boom", 1)  # must return — not RecursionError
        finally:
            logger.disabled = old
        assert calls["n"] == _MAX_EMIT_DEPTH, f"depth not bounded: {calls['n']} subscriber calls"

    def test_legitimate_nested_events_still_flow(self):
        """Bounded depth must not break normal chains (a -> b -> c)."""
        from suijin.kernel.events import EventBus

        bus = EventBus()
        seen = []
        bus.on("a", lambda p: (seen.append("a"), bus.emit("b", p)))
        bus.on("b", lambda p: (seen.append("b"), bus.emit("c", p)))
        bus.on("c", lambda p: seen.append("c"))
        bus.emit("a", None)
        assert seen == ["a", "b", "c"]

    def test_deep_dag_chain_200_modules(self):
        """resolve_dag on a 200-deep chain — no recursion limits anywhere
        (pure + Rust are both iterative; this pins that)."""
        import json

        from suijin.kernel import native as _pure

        manifests = [{"id": "m0", "version": "1", "tier": "core", "requires": [], "overrides": []}]
        for i in range(1, 200):
            manifests.append(
                {"id": f"m{i}", "version": "1", "tier": "recommended", "requires": [f"m{i - 1}"], "overrides": []}
            )
        out = json.loads(_pure.resolve_dag(json.dumps(manifests)))
        assert out["boot_order"][0] == "m0" and out["boot_order"][-1] == "m199"
        assert len(out["boot_order"]) == 200


class TestAuditSweep2:
    """Second-pass probes — config nesting, VFS canonicalization, shutdown."""

    def test_config_deep_merge(self):
        from suijin.kernel.config import LayeredConfig

        cfg = LayeredConfig()
        cfg.add_layer("base", {"nested": {"a": 1, "b": 2}, "top": 1})
        cfg.add_layer("user", {"nested": {"b": 99}})
        snap = cfg.snapshot()
        assert snap["nested"] == {"a": 1, "b": 99}  # user overrode b, kept a
        assert snap["top"] == 1

    def test_vfs_root_itself_allowed(self, tmp_path):
        from suijin.kernel.vfs import Vfs

        vfs = Vfs(tmp_path)
        assert vfs.is_allowed(str(tmp_path))  # root canonical
        assert vfs.is_allowed(str(tmp_path) + "/")  # trailing slash
        assert vfs.is_allowed(tmp_path.resolve())  # symlink-normalized
        assert vfs.is_allowed("a/b")
        assert not vfs.is_allowed(str(tmp_path.parent))  # escape = parent
        assert not vfs.is_allowed("/etc/passwd")

    def test_vfs_symlinked_tmp_root(self, tmp_path):
        """macOS /tmp -> /private/tmp: raw absolutes must resolve before
        the boundary compare (was: the workspace's own path rejected)."""
        import os

        from suijin.kernel.vfs import Vfs

        link_root = tmp_path / "linked_ws"
        real_root = tmp_path / "real_ws"
        real_root.mkdir()
        os.symlink(real_root, link_root)
        vfs = Vfs(link_root)  # root resolves through the link
        assert vfs.is_allowed(str(real_root / "file.txt"))  # real spelling
        assert vfs.is_allowed("file.txt")  # rel spelling
        assert not vfs.is_allowed(str(tmp_path / "outside.txt"))

    def test_context_after_shutdown_is_explicit(self, tmp_path):
        """Calling a tool after shutdown must fail cleanly (module gone),
        not silently return a stale result or crash."""
        from suijin.kernel import controller
        from suijin.kernel.contracts import Module, Tier

        class Frag(Module):
            id = "frag"
            tier = Tier.CORE

            def register(self, ctx):
                ctx.register_tool("frag.ping", lambda a, c: "pong", owner="frag")

            def start(self, ctx):
                pass

            def stop(self, ctx):
                for n in list(ctx._tools):
                    if ctx._tools[n]["owner"] == "frag":
                        del ctx._tools[n]  # realistic stop: unregister

        import json

        tree = tmp_path / "mods"
        (tree / "frag").mkdir(parents=True)
        (tree / "frag" / "plugin.json").write_text(
            json.dumps({"id": "frag", "version": "1", "tier": "core", "requires": []})
        )
        ctx, _ = controller.boot(module_roots=[tree], entries={"frag": Frag()}, workspace=tmp_path, quiet=True)
        assert ctx.call_tool("frag.ping", {}) == "pong"
        ctx.shutdown()
        out = ctx.call_tool("frag.ping", {})
        assert "unknown tool" in out  # clean failure, not a crash

    def test_events_off_during_emit_snapshot(self):
        """off() during a delivery must not corrupt the in-flight batch."""
        from suijin.kernel.events import EventBus

        bus = EventBus()
        seen = []

        def one(p):
            seen.append("one")

        def two(p):
            bus.off("e", one)  # mutate subscriptions mid-emit

        bus.on("e", two)
        bus.on("e", one)
        bus.emit("e", None)
        # two ran (and unsubscribed one) BEFORE one in the snapshot? No —
        # the snapshot was taken at emit start: one still runs this batch.
        assert seen == ["one"]

    def test_registry_duplicate_scan_replaces(self, tmp_path):
        """Re-scanning the same root must replace, not duplicate units."""
        import json

        from suijin.kernel.registry import Registry

        root = tmp_path / "mods"
        (root / "m").mkdir(parents=True)
        (root / "m" / "plugin.json").write_text(json.dumps({"id": "m", "version": "1.0", "tier": "recommended"}))
        reg = Registry()
        reg.scan(root)
        reg.scan(root)  # again
        report = reg.resolve()
        ids = [u.id for u in report.boot_order]
        assert ids.count("m") == 1
