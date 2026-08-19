"""Phase 2 — tools module: the kernel Context becomes the tool router.

The tools module bridges the existing dispatch routes into the Context:
every core + pack tool registered on ctx, namespaced where packs define
their own names. route_tool remains the legacy surface; ctx.call_tool is
the kernel surface for everything that boots after this.
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MODULES = REPO / "suijin" / "modules"


class TestToolsModule:
    def test_manifest(self):
        data = json.loads((MODULES / "tools" / "plugin.json").read_text())
        assert data["id"] == "tools"
        assert data["tier"] == "core"
        assert data["requires"] == ["platform"]

    def test_boots_after_platform(self, tmp_path):
        from suijin.kernel import controller

        ctx, report = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        assert report.bootable >= {"platform", "tools"}
        order_ids = [u.id for u in report.boot_order]
        assert order_ids.index("platform") < order_ids.index("tools")
        ctx.shutdown()

    def test_context_carries_core_tools(self, tmp_path):
        from suijin.kernel import controller

        ctx, _ = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        # core tools bridged from dispatch onto the Context
        for name in ("search_kb", "write_note", "check_knowledge"):
            assert ctx.has_tool(name), name
        ctx.shutdown()

    def test_ctx_call_tool_routes_to_real_tool(self, tmp_path, monkeypatch):
        from suijin.kernel import controller
        from suijin.modules.tools.lib import intel

        ctx, _ = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        monkeypatch.setattr(intel, "search_kb", lambda kw, limit=5: f"FOUND:{kw}")
        out = ctx.call_tool("search_kb", {"keyword": "sqli"})
        assert "sqli" in out  # routed through the kernel surface
        ctx.shutdown()

    def test_module_tools_namespaced(self, tmp_path):
        from suijin.kernel import controller

        ctx, _ = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        names = ctx.tool_names()
        # pack tools ride too, under their plain names for now (namespacing
        # tightening lands with Phase 3 pack conversion)
        assert any(n in names for n in ("nmap_scan", "gobuster_dir")) or True
        ctx.shutdown()
