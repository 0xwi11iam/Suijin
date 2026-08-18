"""Kernel registry — manifest parsing, DAG resolution, classification.

These tests simulate the whole zoo of module trees the controller can
meet in the wild (healthy, missing-dep, circular, collision, broken)
and pin exactly how each classifies.
"""

import json

from suijin.kernel.registry import Registry


def manifest(mid, **kw):
    return {
        "id": mid,
        "version": "1.0.0",
        "tier": kw.pop("tier", "recommended"),
        "requires": kw.pop("requires", []),
        **kw,
    }


class TestRegistryResolve:
    def test_healthy_tree_boot_order(self):
        reg = Registry()
        for m in (
            manifest("platform", tier="core"),
            manifest("tools", tier="core", requires=["platform"]),
            manifest("providers", requires=["platform"]),
            manifest("agent", tier="core", requires=["tools", "providers"]),
        ):
            reg.add_manifest(m)
        report = reg.resolve()
        order = [u.id for u in report.boot_order]
        assert order.index("platform") < order.index("tools") < order.index("agent")
        assert report.bootable == {"platform", "tools", "providers", "agent"}

    def test_missing_dependency_skips_module_not_boot(self):
        reg = Registry()
        reg.add_manifest(manifest("platform", tier="core"))
        reg.add_manifest(manifest("knowledge", requires=["providers"]))  # never added
        report = reg.resolve()
        assert "knowledge" not in report.bootable
        assert report.skipped["knowledge"].startswith("missing dependency: providers")
        assert "platform" in report.bootable  # boot continues

    def test_missing_core_dependency_aborts(self):
        reg = Registry()
        reg.add_manifest(manifest("tools", tier="core", requires=["ghost"]))
        report = reg.resolve()
        assert report.aborted is True
        assert "tools" in report.abort_reason

    def test_circular_dependency_named(self):
        reg = Registry()
        reg.add_manifest(manifest("a", requires=["b"]))
        reg.add_manifest(manifest("b", requires=["a"]))
        report = reg.resolve()
        assert report.aborted is False  # recommended-tier circularity skips both
        assert "a" in report.skipped and "b" in report.skipped
        assert "cycle" in report.skipped["a"]

    def test_circular_core_aborts(self):
        reg = Registry()
        reg.add_manifest(manifest("x", tier="core", requires=["y"]))
        reg.add_manifest(manifest("y", tier="core", requires=["x"]))
        report = reg.resolve()
        assert report.aborted is True
        assert "cycle" in report.abort_reason

    def test_name_collision_later_tier_loses(self):
        reg = Registry()
        reg.add_manifest(manifest("nmap", tier="recommended"))
        reg.add_manifest(manifest("nmap", tier="installed"))
        report = reg.resolve()
        winner = report.units["nmap"]
        assert winner.tier.value == 1  # recommended won
        assert report.collisions == [("nmap", "installed")]

    def test_override_flag_allows_shadowing(self):
        reg = Registry()
        reg.add_manifest(manifest("nmap", tier="recommended"))
        reg.add_manifest(manifest("nmap", tier="installed", overrides=["nmap"]))
        report = reg.resolve()
        assert report.units["nmap"].tier.value == 2  # installed won via override
        assert report.overridden == ["nmap"]


class TestRegistrySearchPaths:
    def test_scan_discovers_trees(self, tmp_path):
        (tmp_path / "alpha").mkdir()
        (tmp_path / "alpha" / "plugin.json").write_text(json.dumps(manifest("alpha")))
        (tmp_path / "beta").mkdir()
        (tmp_path / "beta" / "plugin.json").write_text(json.dumps(manifest("beta", requires=["alpha"])))
        (tmp_path / "not_a_module.txt").write_text("x")
        reg = Registry()
        found = reg.scan(tmp_path)
        assert found == {"alpha", "beta"}

    def test_later_source_overrides_earlier(self, tmp_path):
        first, second = tmp_path / "wheel", tmp_path / "user"
        for d, ver in ((first, "1.0.0"), (second, "2.0.0")):
            (d / "mod").mkdir(parents=True)
            (d / "mod" / "plugin.json").write_text(json.dumps(manifest("mod", version=ver)))
        reg = Registry()
        reg.scan(first)
        reg.scan(second)  # later wins
        report = reg.resolve()
        assert report.units["mod"].version == "2.0.0"
        assert report.collisions == []  # same id different SOURCE is replacement

    def test_broken_manifest_quarantined(self, tmp_path):
        (tmp_path / "brokenmod").mkdir()
        (tmp_path / "brokenmod" / "plugin.json").write_text("{not json")
        (tmp_path / "good").mkdir()
        (tmp_path / "good" / "plugin.json").write_text(json.dumps(manifest("good")))
        reg = Registry()
        reg.scan(tmp_path)
        report = reg.resolve()
        assert "good" in report.bootable
        assert "brokenmod" in report.quarantined


class TestReport:
    def test_summary_lines(self):
        reg = Registry()
        reg.add_manifest(manifest("platform", tier="core"))
        reg.add_manifest(manifest("knowledge", requires=["ghost"]))
        report = reg.resolve()
        summary = report.summary()
        assert "1 module(s) loaded" in summary
        assert "knowledge" in summary and "missing" in summary
