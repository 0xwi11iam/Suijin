"""Phase 2 — agent module (NESTED): the registry must recurse once so
sub-modules (agent/graph, agent/nodes, agent/memory) are first-class DAG
units, and the parent composes them via requires.
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MODULES = REPO / "suijin" / "modules"


class TestNestedScan:
    def test_scan_recurses_one_level(self):
        from suijin.kernel.registry import Registry

        reg = Registry()
        found = reg.scan(MODULES)
        for expected in ("agent", "agent.graph", "agent.nodes", "agent.memory"):
            assert expected in found, expected

    def test_nested_units_resolve(self):
        from suijin.kernel.registry import Registry

        reg = Registry()
        reg.scan(MODULES)
        report = reg.resolve()
        assert not report.aborted
        order_ids = [u.id for u in report.boot_order]
        # sub-modules boot BEFORE the parent agent (it requires them)
        assert order_ids.index("agent.graph") < order_ids.index("agent")
        assert order_ids.index("agent.nodes") < order_ids.index("agent")
        assert order_ids.index("agent.memory") < order_ids.index("agent")

    def test_parent_manifest_declares_children(self):
        parent = json.loads((MODULES / "agent" / "plugin.json").read_text())
        assert parent["id"] == "agent"
        assert parent["tier"] == "core"
        assert set(parent.get("modules", [])) == {"graph", "nodes", "memory"}
        # children carry the dotted id
        graph = json.loads((MODULES / "agent" / "graph" / "plugin.json").read_text())
        assert graph["id"] == "agent.graph"


class TestAgentModuleBoots:
    def test_full_boot_includes_agent(self, tmp_path):
        from suijin.kernel import controller

        ctx, report = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        order_ids = [u.id for u in report.boot_order]
        assert {"platform", "tools", "agent.graph", "agent.nodes", "agent.memory", "agent"} <= set(order_ids)
        # the agent service materializes (the run loop factory)
        assert ctx.has_service("agent_graph")
        ctx.shutdown()

    def test_submodule_entries_materialize(self, tmp_path):
        from suijin.kernel import controller

        ctx, _ = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        assert any(u.id == "agent.graph" for u in controller._LAST_BOOT_ENTRIES.values()) or "agent.graph" in {
            getattr(m, "id", "") for m in controller._LAST_BOOT_ENTRIES.values()
        }
        ctx.shutdown()
