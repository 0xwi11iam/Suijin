"""Kernel controller — boot composition + the quiet-boot contract."""

import json

import pytest

from suijin.kernel import controller
from suijin.kernel.contracts import Tier
from suijin.kernel.controller import boot


class DemoModule:
    """Conforming module used across controller tests."""

    def __init__(self, mid, tier=Tier.RECOMMENDED, requires=(), boom_register=False, boom_start=False, tools=()):
        self.id = mid
        self.tier = tier
        self.requires = list(requires)
        self.boom_register = boom_register
        self.boom_start = boom_start
        self.tools = list(tools)
        self.registered = False
        self.started = False
        self.stopped = False

    def register(self, ctx):
        if self.boom_register:
            raise RuntimeError("register exploded")
        self.registered = True
        for tname in self.tools:
            ctx.register_tool(
                tname, lambda args, c, n=tname: f"{n} ran", description=f"tool from {self.id}", owner=self.id
            )

    def start(self, ctx):
        if self.boom_start:
            raise RuntimeError("start exploded")
        self.started = True

    def stop(self, ctx):
        self.stopped = True


class TestBoot:
    def test_healthy_boot(self, tmp_path):
        tree = tmp_path / "modules"
        for mid, requires in (("platform", []), ("tools", ["platform"]), ("agent", ["tools"])):
            d = tree / mid
            d.mkdir(parents=True)
            (d / "plugin.json").write_text(
                json.dumps(
                    {
                        "id": mid,
                        "version": "1.0.0",
                        "tier": "core" if mid in ("platform", "tools", "agent") else "recommended",
                        "requires": requires,
                    }
                )
            )
        ctx, report = boot(
            module_roots=[tree],
            entries={
                "platform": DemoModule("platform", tier=Tier.CORE),
                "tools": DemoModule("tools", tier=Tier.CORE, requires=["platform"], tools=["demo.scan"]),
                "agent": DemoModule("agent", tier=Tier.CORE, requires=["tools"]),
            },
        )
        assert not report.aborted
        assert ctx.call_tool("demo.scan", {}) == "demo.scan ran"
        # entry objects registered AND started, in dependency order
        assert controller._LAST_BOOT_ENTRIES["tools"].started
        assert controller._LAST_BOOT_ENTRIES["agent"].started

    def test_quiet_when_healthy(self, tmp_path, capsys):
        tree = tmp_path / "m"
        (tree / "solo").mkdir(parents=True)
        (tree / "solo" / "plugin.json").write_text(json.dumps({"id": "solo", "tier": "core"}))
        boot(module_roots=[tree], entries={"solo": DemoModule("solo", tier=Tier.CORE)}, quiet=True)
        out = capsys.readouterr().out
        assert out == ""  # quiet boot: silent when healthy

    def test_loud_when_skipped(self, tmp_path, capsys):
        tree = tmp_path / "m"
        (tree / "solo").mkdir(parents=True)
        (tree / "solo" / "plugin.json").write_text(json.dumps({"id": "solo", "tier": "core"}))
        (tree / "needy").mkdir(parents=True)
        (tree / "needy" / "plugin.json").write_text(
            json.dumps({"id": "needy", "tier": "recommended", "requires": ["ghost"]})
        )
        boot(
            module_roots=[tree],
            entries={"solo": DemoModule("solo", tier=Tier.CORE), "needy": DemoModule("needy", requires=["ghost"])},
            quiet=True,
        )
        out = capsys.readouterr().out
        assert "needy" in out and "skipped" in out  # report shown because problems exist

    def test_missing_entry_quarantined_not_fatal(self, tmp_path):
        tree = tmp_path / "m"
        (tree / "solo").mkdir(parents=True)
        (tree / "solo" / "plugin.json").write_text(json.dumps({"id": "solo", "tier": "core"}))
        (tree / "noentry").mkdir(parents=True)
        (tree / "noentry" / "plugin.json").write_text(
            json.dumps({"id": "noentry", "tier": "recommended", "entry": "ghost:Mod"})
        )
        ctx, report = boot(module_roots=[tree], entries={"solo": DemoModule("solo", tier=Tier.CORE)})
        assert not report.aborted
        assert "noentry" in report.quarantined

    def test_start_failure_skips_module_boot_continues(self, tmp_path):
        tree = tmp_path / "m"
        (tree / "core1").mkdir(parents=True)
        (tree / "core1" / "plugin.json").write_text(json.dumps({"id": "core1", "tier": "core"}))
        (tree / "brittle").mkdir(parents=True)
        (tree / "brittle" / "plugin.json").write_text(json.dumps({"id": "brittle", "tier": "recommended"}))
        ctx, report = boot(
            module_roots=[tree],
            entries={"core1": DemoModule("core1", tier=Tier.CORE), "brittle": DemoModule("brittle", boom_start=True)},
        )
        assert not report.aborted
        assert "brittle" in report.skipped
        assert "start exploded" in report.skipped["brittle"]

    def test_core_abort_raises(self, tmp_path):
        tree = tmp_path / "m"
        (tree / "core1").mkdir(parents=True)
        (tree / "core1" / "plugin.json").write_text(json.dumps({"id": "core1", "tier": "core", "requires": ["ghost"]}))
        with pytest.raises(RuntimeError, match="core module"):
            boot(module_roots=[tree], entries={})

    def test_shutdown_calls_stop(self, tmp_path):
        tree = tmp_path / "m"
        (tree / "solo").mkdir(parents=True)
        (tree / "solo" / "plugin.json").write_text(json.dumps({"id": "solo", "tier": "core"}))
        mod = DemoModule("solo", tier=Tier.CORE)
        ctx, report = boot(module_roots=[tree], entries={"solo": mod})
        ctx.shutdown()
        assert mod.stopped
