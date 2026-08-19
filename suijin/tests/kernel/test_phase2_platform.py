"""Phase 2 — platform module boots through the kernel controller.

The first real core-tier module: controller.boot() scans the modules/
tree, resolves the manifest, materializes PlatformModule via its entry
string (no injected objects — the manifest IS the source now), and the
Context comes out carrying the platform services.
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MODULES = REPO / "suijin" / "modules"


class TestPlatformModule:
    def test_manifest_valid(self):
        data = json.loads((MODULES / "platform" / "plugin.json").read_text())
        assert data["id"] == "platform"
        assert data["tier"] == "core"
        assert data["entry"] == "suijin.modules.platform:PlatformModule"

    def test_entry_materializes(self):
        from suijin.kernel.controller import _import_entry

        obj = _import_entry("suijin.modules.platform:PlatformModule")
        assert obj is not None and obj.id == "platform"

    def test_boots_via_controller(self, tmp_path, capsys):
        from suijin.kernel import controller

        ctx, report = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        assert not report.aborted
        assert "platform" in report.bootable  # tools module joins the tree later
        # services registered and materialize lazily
        assert ctx.has_service("config")
        assert ctx.has_service("workspace")
        # start() ran: workspace dirs exist, journal recorded
        assert (tmp_path / "reports").is_dir()
        assert (tmp_path / "audit_trails").is_dir()
        assert any("workspace ready" in ln for ln in ctx.journal.tail(200))  # 49 vendored packs journal too
        # quiet boot: healthy => silent
        assert capsys.readouterr().out == ""
        ctx.shutdown()

    def test_services_materialize(self, tmp_path):
        from suijin.kernel import controller

        ctx, _ = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        # workspace service points at the boot workspace
        assert str(ctx.service("workspace")) == str(tmp_path)
        ctx.shutdown()

    def test_idempotent_start(self, tmp_path):
        from suijin.kernel import controller

        ctx, _ = controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)
        # second start (e.g. forced re-boot in tests) is a no-op, not an error
        ctx.service("workspace")
        ctx.shutdown()


class TestModulesTreeScanned:
    def test_scan_finds_platform(self):
        from suijin.kernel.registry import Registry

        reg = Registry()
        found = reg.scan(MODULES)
        assert "platform" in found
