"""Kernel integration — one boot exercising every subsystem together.

The 'OS POST': scan a realistic tree, boot it, verify the context came
out fully formed (tools + services + events + jobs + vfs + journal +
health), degrade it, and shut it down.
"""

import json

from suijin.kernel import controller
from suijin.kernel.contracts import Tier


class Mod:
    def __init__(
        self, mid, tier=Tier.RECOMMENDED, requires=(), provides=(), tools=(), service_name=None, subscribe=None
    ):
        self.id = mid
        self.tier = tier
        self.requires = list(requires)
        self.provides = list(provides)
        self.tools = list(tools)
        self.service_name = service_name
        self.subscribe = subscribe
        self.events_seen = []
        self.stopped = False

    def register(self, ctx):
        for t in self.tools:
            ctx.register_tool(
                t, lambda args, c, n=t: f"{n}:{args.get('x', '')}", description=f"from {self.id}", owner=self.id
            )
        if self.service_name:
            ctx.register_service(self.service_name, lambda: f"{self.service_name}-obj")
        if self.subscribe:
            ctx.on_event(self.subscribe, lambda p: self.events_seen.append(p))

    def start(self, ctx):
        if self.service_name:  # prove services materialize during start
            ctx.emit("started", self.id)

    def stop(self, ctx):
        self.stopped = True


def _tree(tmp_path):
    root = tmp_path / "modules"
    specs = [
        ("platform", "core", []),
        ("tools", "core", ["platform"]),
        ("providers", "recommended", ["platform"]),
        ("redteam", "recommended", ["tools", "providers"]),
        ("broken-extra", "recommended", ["ghost-dep"]),
    ]
    for mid, tier, requires in specs:
        d = root / mid
        d.mkdir(parents=True)
        (d / "plugin.json").write_text(json.dumps({"id": mid, "version": "1.0.0", "tier": tier, "requires": requires}))
    return root


class TestFullBoot:
    def test_post(self, tmp_path, capsys):
        entries = {
            "platform": Mod("platform", tier=Tier.CORE),
            "tools": Mod("tools", tier=Tier.CORE, requires=["platform"], tools=["demo.scan"], subscribe="started"),
            "providers": Mod("providers", service_name="llm"),
            "redteam": Mod("redteam", requires=["tools", "providers"], tools=["red.attack"]),
        }
        ctx, report = controller.boot(module_roots=[_tree(tmp_path)], entries=entries, workspace=tmp_path, quiet=True)

        # registry: everything bootable except the broken extra
        assert report.bootable == {"platform", "tools", "providers", "redteam"}
        assert "broken-extra" in report.skipped

        # context: tools routed, owned, isolated
        assert ctx.call_tool("demo.scan", {"x": 1}) == "demo.scan:1"
        assert ctx.call_tool("red.attack", {"x": 2}) == "red.attack:2"
        assert ctx.tool_names(owner="redteam") == ["red.attack"]

        # services: lazy singleton materialized
        assert ctx.service("llm") == "llm-obj"

        # events: cross-module delivery fired during providers.start()
        assert entries["tools"].events_seen == ["providers"]

        # jobs: kernel scheduler live on ctx
        jid = ctx.jobs.spawn("t", {}, fn=lambda n, a, c: "ok")
        import time as _t

        _t.sleep(0.2)
        assert ctx.jobs.status(jid) in ("done", "ok")

        # vfs: workspace-anchored, escapes refused
        assert ctx.vfs.is_allowed("reports/r.md")
        assert not ctx.vfs.is_allowed("../escape.txt")
        ctx.vfs.open_for_write("logs/note.txt", "data")

        # journal + health: boot recorded, degradation recorded
        assert any("module.start" in ln for ln in ctx.journal.tail(20))
        assert ctx.health.get("broken-extra")["status"] == "skipped"
        assert ctx.health.summary().get("ok") == 4

        # quiet boot: output ONLY because of the degraded module
        out = capsys.readouterr().out
        assert "broken-extra" in out

        # shutdown: reverse order, journal flushed
        ctx.shutdown()
        assert entries["redteam"].stopped and entries["platform"].stopped
        disk = (tmp_path / "logs" / "journal.log").read_text()
        assert "shutdown" in disk and "module.start" in disk
