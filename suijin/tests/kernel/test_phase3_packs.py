"""Phase 3b — convert the Modules/ packs to kernel plugins.

A converter generates plugin.json for every pack (id, tier=recommended,
requires=[platform], permissions derived from declared binaries + tool
surface), the registry scans Modules/ as a module root alongside
suijin/modules/, and the kernel boots them as first-class units.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MODULES_ROOT = REPO / "Modules"
CONVERTER = REPO / "suijin" / "modules" / "pack_converter.py"


class TestPackConverter:
    def test_converter_exists_and_runs(self, tmp_path):
        out = tmp_path / "packs"
        r = subprocess.run(
            [sys.executable, str(CONVERTER), "--source", str(MODULES_ROOT),
             "--dest", str(out)],
            capture_output=True, text=True, timeout=120, cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        manifests = list(out.rglob("plugin.json"))
        assert len(manifests) >= 40, f"expected ~49 packs, got {len(manifests)}"

    def test_converted_shape(self, tmp_path):
        out = tmp_path / "packs"
        subprocess.run(
            [sys.executable, str(CONVERTER), "--source", str(MODULES_ROOT),
             "--dest", str(out)],
            capture_output=True, text=True, timeout=120, cwd=str(REPO), check=True)
        nmap = json.loads((out / "nmap" / "plugin.json").read_text())
        assert nmap["id"] == "nmap"
        assert nmap["tier"] == "recommended"
        assert "platform" in nmap["requires"]
        assert nmap["entry"]  # generated shim path
        assert nmap["permissions"]  # shell for nmap at minimum

    def test_id_collision_between_packs_detected(self, tmp_path):
        from suijin.modules.pack_converter import convert_tree

        src = tmp_path / "src"
        (src / "Tools" / "dup").mkdir(parents=True)
        (src / "Tools" / "dup" / "manifest.json").write_text(json.dumps(
            {"name": "dup", "version": "1", "tools": {"dup_run": {"description": "x"}},
             "dependencies": []}))
        (src / "Mods" / "dup").mkdir(parents=True)
        (src / "Mods" / "dup" / "manifest.json").write_text(json.dumps(
            {"name": "dup", "version": "1", "tools": {}, "dependencies": []}))
        result = convert_tree(src, tmp_path / "out")
        assert "dup" in result.collisions


class TestPacksBoot:
    def test_registry_scans_converted_packs(self, tmp_path):
        from suijin.kernel.registry import Registry
        from suijin.modules.pack_converter import convert_tree

        packs = tmp_path / "packs"
        convert_tree(MODULES_ROOT, packs)
        reg = Registry()
        found_packs = reg.scan(packs)
        assert len(found_packs) >= 40
        report = reg.resolve()
        assert not report.aborted
        # packs require only platform (which isn't in this tree) — all skip
        # unless platform present; add it via suijin/modules and re-scan
        reg.scan(REPO / "suijin" / "modules")
        report = reg.resolve()
        assert not report.aborted
        assert any(u.id == "nmap" for u in report.boot_order)
