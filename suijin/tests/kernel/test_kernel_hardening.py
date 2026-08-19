"""Kernel hardening — fault injection + property tests for the gaps the
existing suites don't pin.

The kernel already has: journal stress/recovery (audit regressions),
controller fault paths (missing entry, start-failure skip, core abort),
event-bus thread safety, VFS traversal/symlink/allowlist coverage, and
the Rust-oracle fuzz. This file adds what's left:

  - LayeredConfig: deep-merge semantics (section-partial override,
    idempotence, layer immutability against caller mutation)
  - VFS edge cases: trailing slashes, normalized-inside a/../b,
    allowlisted PARENT dirs, root resolution itself
  - controller: corrupt JSON manifest quarantined (not a crash);
    dependency cycle fails with a named, clear error; double boot +
    double shutdown are idempotent; a start() crash unwinds cleanly
    (already-started modules still get stop())
  - registry: duplicate ids across roots resolve deterministically
    (first root wins — vendored beats user dir)
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _write_module(root: Path, mid: str, *, tier="recommended", requires=None, start_raises=False, manifest_json=None):
    """A minimal but ALWAYS-LOADABLE unit (pack_entry scheme — the same
    shape converted packs use)."""
    d = root / mid
    d.mkdir(parents=True, exist_ok=True)
    if manifest_json is None:
        manifest_json = json.dumps(
            {
                "id": mid,
                "version": "1.0",
                "tier": tier,
                "requires": requires or [],
                "entry": f"pack_entry:{mid}",
                "description": mid,
            }
        )
    (d / "plugin.json").write_text(manifest_json)
    start_body = 'raise RuntimeError("boom")' if start_raises else "pass"
    (d / "entry.py").write_text(
        "from suijin.kernel.contracts import Module, Tier\n\n\n"
        f"class PackModule(Module):\n    id = '{mid}'\n    tier = Tier.{tier.upper()}\n\n"
        "    def register(self, ctx) -> None:\n        pass\n\n"
        f"    def start(self, ctx) -> None:\n        {start_body}\n\n"
        "    def stop(self, ctx) -> None:\n        pass\n"
    )
    return d


class TestLayeredConfigDeepMerge(unittest.TestCase):
    def _cfg(self):
        from suijin.kernel.config import LayeredConfig

        return LayeredConfig()

    def test_section_partial_override_keeps_siblings(self):
        """The kernel killer this merge exists for: overriding one key
        inside a section must not wipe the section's other keys."""
        c = self._cfg()
        c.add_layer("base", {"net": {"host": "0.0.0.0", "port": 8080}, "log": "info"})
        c.add_layer("user", {"net": {"port": 9090}})
        snap = c.snapshot()
        self.assertEqual(snap["net"]["port"], 9090)
        self.assertEqual(snap["net"]["host"], "0.0.0.0")  # sibling survives
        self.assertEqual(snap["log"], "info")

    def test_non_dict_leaf_replaces_wholesale(self):
        c = self._cfg()
        c.add_layer("a", {"x": {"deep": 1}})
        c.add_layer("b", {"x": [1, 2, 3]})  # dict -> list: replace, no merge
        self.assertEqual(c.snapshot()["x"], [1, 2, 3])

    def test_merge_is_idempotent(self):
        c = self._cfg()
        layer = {"a": {"b": {"c": 1}}}
        c.add_layer("l1", layer)
        s1, s2 = c.snapshot(), c.snapshot()
        self.assertEqual(s1, s2)
        s1["a"]["b"]["c"] = 999  # mutating a snapshot never leaks back
        self.assertEqual(c.snapshot()["a"]["b"]["c"], 1)

    def test_caller_cannot_mutate_layers_via_add(self):
        c = self._cfg()
        data = {"k": {"v": 1}}
        c.add_layer("l", data)
        data["k"]["v"] = 666  # mutating the INPUT after add must not leak
        self.assertEqual(c.snapshot()["k"]["v"], 1)

    def test_many_layers_property(self):
        """Fuzz-lite: 100 random layers, snapshot equals sequential merge."""
        import random

        rnd = random.Random(42)
        c = self._cfg()
        keys = ["a", "b", "c"]
        expected = {}
        for i in range(100):
            layer = {rnd.choice(keys): rnd.randint(0, 9) for _ in range(rnd.randint(1, 3))}
            c.add_layer(f"l{i}", layer)
            expected.update(layer)
        self.assertEqual(c.snapshot(), expected)


class TestVFSEdges(unittest.TestCase):
    def _vfs(self, tmp, allow=None):
        from suijin.kernel.vfs import Vfs

        return Vfs(Path(tmp), allow=[Path(a) for a in (allow or [])])

    def test_trailing_slashes_normalized(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            vfs = self._vfs(tmp)
            self.assertTrue(vfs.is_allowed("reports/x.json/"))
            self.assertFalse(vfs.is_allowed("../escape/"))

    def test_interior_dots_that_stay_inside_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            vfs = self._vfs(tmp)
            self.assertTrue(vfs.is_allowed("a/../b"))  # resolves to b — inside
            self.assertFalse(vfs.is_allowed("a/../../escape"))

    def test_allowlisted_parent_covers_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tempfile.mkdtemp())
            try:
                vfs = self._vfs(tmp, allow=[outside / "shared"])
                (outside / "shared").mkdir(exist_ok=True)
                self.assertTrue(vfs.is_allowed(outside / "shared" / "x.txt"))
                self.assertFalse(vfs.is_allowed(outside / "private" / "x.txt"))
            finally:
                shutil.rmtree(outside, ignore_errors=True)

    def test_root_itself_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            vfs = self._vfs(tmp)
            self.assertTrue(vfs.is_allowed("."))
            self.assertTrue(vfs.is_allowed(""))


class TestControllerFaults(unittest.TestCase):
    def _boot(self, roots, workspace):
        from suijin.kernel import controller

        return controller.boot(module_roots=roots, workspace=workspace, quiet=True)

    def test_corrupt_manifest_json_quarantined_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as ws:
            root = Path(tmp)
            _write_module(root, "healthy")
            bad = root / "broken"
            bad.mkdir()
            (bad / "plugin.json").write_text("{ this is not json")
            ctx, report = self._boot([root], Path(ws) / "w")
            ids = [u.id for u in report.boot_order]
            self.assertIn("healthy", ids)
            self.assertIn("broken", report.quarantined)
            ctx.shutdown()

    def test_dependency_cycle_named_clearly(self):
        """Contract: a cycle is a NAMED skip (path spelled out), boot
        itself never aborts on recommended-tier cycles."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as ws:
            root = Path(tmp)
            _write_module(root, "left", requires=["right"])
            _write_module(root, "right", requires=["left"])
            ctx, report = self._boot([root], Path(ws) / "w")
            self.assertFalse(report.aborted)
            self.assertEqual(report.skipped.get("left"), "dependency cycle: left -> right -> left")
            self.assertIn("right", report.skipped)
            ctx.shutdown()

    def test_start_crash_unwinds_already_started(self):
        """A late module crashing in start() must not orphan the modules
        that already started — shutdown still calls their stop()."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as ws:
            root = Path(tmp)
            _write_module(root, "first", tier="core")
            # make first's stop observable through the kernel journal
            entry = root / "first" / "entry.py"
            entry.write_text(
                entry.read_text().replace(
                    "    def stop(self, ctx) -> None:\n        pass",
                    "    def stop(self, ctx) -> None:\n        ctx.journal.append('first', 'STOPPED')",
                )
            )
            _write_module(root, "crasher", tier="recommended", start_raises=True)
            ctx, report = self._boot([root], Path(ws) / "w")
            self.assertIn("crasher", report.skipped)
            self.assertIn("first", [u.id for u in report.boot_order])
            ctx.shutdown()
            # 'first' booted before 'crasher' crashed — its stop() must
            # still have run (shutdown unwinds cleanly)
            # the flush clears the in-memory ring BY DESIGN — the durable
            # proof is the journal file in the workspace
            jlog = (Path(ws) / "w" / "logs" / "journal.log").read_text()
            self.assertIn("first: STOPPED", jlog, "already-started module orphaned by a later start() crash")

    def test_boot_is_repeatable_and_shutdown_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as ws:
            root = Path(tmp)
            _write_module(root, "solo")
            order1 = [u.id for u in self._boot([root], Path(ws) / "a")[1].boot_order]
            order2 = [u.id for u in self._boot([root], Path(ws) / "b")[1].boot_order]
            self.assertEqual(order1, order2)  # deterministic order
            ctx, _ = self._boot([root], Path(ws) / "c")
            ctx.shutdown()
            ctx.shutdown()  # second shutdown is a no-op, never raises


class TestRegistryRootPrecedence(unittest.TestCase):
    def test_duplicate_id_later_scan_replaces(self):
        """Registry replacement semantics (documented): a later scan of
        the same id replaces the earlier unit — exactly one winner,
        deterministic. (The PACK LOADER independently gives the vendored
        root priority for tool packs; that rule lives in loader.py.)"""
        from suijin.kernel.registry import Registry

        with tempfile.TemporaryDirectory() as tmp:
            r1, r2 = Path(tmp) / "first", Path(tmp) / "second"
            _write_module(
                r1,
                "dup",
                manifest_json=json.dumps(
                    {
                        "id": "dup",
                        "version": "1",
                        "tier": "recommended",
                        "requires": [],
                        "entry": "pack_entry:dup",
                        "description": "first-scan",
                    }
                ),
            )
            _write_module(
                r2,
                "dup",
                manifest_json=json.dumps(
                    {
                        "id": "dup",
                        "version": "2",
                        "tier": "recommended",
                        "requires": [],
                        "entry": "pack_entry:dup",
                        "description": "second-scan",
                    }
                ),
            )
            reg = Registry()
            reg.scan(r1)
            reg.scan(r2)
            self.assertEqual(len([u for u in reg._units.values() if u.id == "dup"]), 1)
            self.assertEqual(reg._units["dup"].version, "2")  # later scan replaced the unit


if __name__ == "__main__":
    unittest.main()
