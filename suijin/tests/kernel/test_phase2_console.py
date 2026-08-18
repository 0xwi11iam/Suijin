"""Phase 2 — console module: feature-blind core with extension hooks.

Console owns the SURFACES (menus, verbs), not the features: modules
register menu entries and CLI verbs as hooks on the Context, and console
renders whatever is registered. A disabled module's entries genuinely
disappear — that's the proof the architecture is real.
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MODULES = REPO / "suijin" / "modules"


class TestConsoleModule:
    def test_manifest(self):
        data = json.loads((MODULES / "console" / "plugin.json").read_text())
        assert data["id"] == "console"
        assert data["tier"] == "core"
        assert "agent" in data["requires"]

    def test_hooks_registry_on_context(self, tmp_path):
        from suijin.kernel import controller

        ctx, _ = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        # the hook registry is a Context service
        hooks = ctx.service("console_hooks")
        assert hooks is not None
        ctx.shutdown()

    def test_menu_registration_and_listing(self, tmp_path):
        from suijin.kernel import controller

        ctx, _ = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        hooks = ctx.service("console_hooks")
        hooks.register_menu("zz-manual", label="Manual", order=5)
        entries = hooks.menu()
        ids = [e["id"] for e in entries]
        # order honored AND boot-time entries (mode modules) present
        assert ids[0] == "zz-manual"  # order 5 beats everything
        assert {"redteam", "blueteam"} <= set(ids)  # from Phase 3 modules
        ctx.shutdown()

    def test_verb_registration(self, tmp_path):
        from suijin.kernel import controller

        ctx, _ = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        hooks = ctx.service("console_hooks")
        calls = []
        hooks.register_verb("battle", lambda: calls.append("ran") or "ok")
        assert hooks.run_verb("battle") == "ok"
        assert calls == ["ran"]
        assert hooks.run_verb("never-registered") is None
        ctx.shutdown()

    def test_disabled_module_entries_vanish(self, tmp_path):
        """The architecture proof: unregister (module stopped) => entries gone."""
        from suijin.kernel import controller

        ctx, _ = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        hooks = ctx.service("console_hooks")
        hooks.register_menu("demo", label="Demo", order=1)
        assert any(e["id"] == "demo" for e in hooks.menu())
        hooks.unregister_owner("demo")
        assert not any(e["id"] == "demo" for e in hooks.menu())
        ctx.shutdown()

    def test_full_core_tier_boots(self, tmp_path):
        from suijin.kernel import controller

        ctx, report = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        order_ids = [u.id for u in report.boot_order]
        assert {"platform", "tools", "agent.graph", "agent.nodes", "agent.memory", "agent", "console"} <= set(order_ids)
        ctx.shutdown()
