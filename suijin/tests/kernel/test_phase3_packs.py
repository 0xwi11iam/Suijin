"""Phase 3b — the pack converter + the vendored pack estate.

The converter turns legacy pack trees (manifest.json format) into
kernel-bootable units (plugin.json + entry.py). Since v4.1 the repo's
packs are VENDORED into suijin/modules/ — the legacy Modules/ tree is
gone. These tests keep the converter honest (users convert their own
trees) and pin the vendored estate's shape.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CONVERTER = REPO / "suijin" / "modules" / "pack_converter.py"
VENDORED = REPO / "suijin" / "modules"


def _fixture_source(root: Path) -> Path:
    """A minimal legacy pack tree: two packs + one broken manifest."""
    src = root / "src"
    (src / "Tools" / "alpha").mkdir(parents=True)
    (src / "Tools" / "alpha" / "manifest.json").write_text(
        json.dumps(
            {
                "name": "Alpha",
                "version": "1",
                "description": "alpha test pack",
                "tools": {"alpha_run": {"description": "run alpha"}},
                "dependencies": ["nmap"],
            }
        )
    )
    (src / "Tools" / "alpha" / "main.py").write_text(
        'def alpha_run(target: str = "") -> str:\n    return f"alpha:{target}"\n'
    )
    (src / "Mods" / "beta").mkdir(parents=True)
    (src / "Mods" / "beta" / "manifest.json").write_text(
        json.dumps({"name": "Beta", "version": "1", "tools": {"beta_run": {"description": "x"}}, "dependencies": []})
    )
    (src / "Mods" / "beta" / "main.py").write_text('def beta_run() -> str:\n    return "beta"\n')
    return src


class TestPackConverter:
    def test_converter_runs_on_fixture(self, tmp_path):
        out = tmp_path / "packs"
        r = subprocess.run(
            [sys.executable, str(CONVERTER), "--source", str(_fixture_source(tmp_path)), "--dest", str(out)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(REPO),
        )
        assert r.returncode == 0, r.stderr
        assert (out / "alpha" / "plugin.json").exists()
        assert (out / "beta" / "plugin.json").exists()

    def test_converted_shape(self, tmp_path):
        from suijin.modules.pack_converter import convert_tree

        out = tmp_path / "packs"
        convert_tree(_fixture_source(tmp_path), out)
        alpha = json.loads((out / "alpha" / "plugin.json").read_text())
        assert alpha["id"] == "alpha"
        assert alpha["tier"] == "recommended"
        assert "platform" in alpha["requires"]
        assert alpha["entry"] == "pack_entry:alpha"
        assert "shell" in alpha["permissions"]  # nmap dependency implies shell
        assert (out / "alpha" / "entry.py").exists()
        assert (out / "alpha" / "__init__.py").exists()  # wheel-shippable
        # the empty-args bridge regression ({{}} set-of-dict bug)
        entry_src = (out / "alpha" / "entry.py").read_text()
        assert "{{}}" not in entry_src

    def test_id_collision_between_packs_detected(self, tmp_path):
        from suijin.modules.pack_converter import convert_tree

        src = tmp_path / "src"
        (src / "Tools" / "dup").mkdir(parents=True)
        (src / "Tools" / "dup" / "manifest.json").write_text(
            json.dumps({"name": "dup", "version": "1", "tools": {"dup_run": {"description": "x"}}, "dependencies": []})
        )
        (src / "Mods" / "dup").mkdir(parents=True)
        (src / "Mods" / "dup" / "manifest.json").write_text(
            json.dumps({"name": "dup", "version": "1", "tools": {}, "dependencies": []})
        )
        result = convert_tree(src, tmp_path / "out")
        assert "dup" in result.collisions


class TestPacksBoot:
    def test_registry_scans_converted_packs(self, tmp_path):
        from suijin.kernel.registry import Registry
        from suijin.modules.pack_converter import convert_tree

        packs = tmp_path / "packs"
        convert_tree(_fixture_source(tmp_path), packs)
        reg = Registry()
        found = reg.scan(packs)
        assert len(found) == 2
        report = reg.resolve()
        assert not report.aborted
        # packs require platform (absent from this tree) — skipped until the
        # first-party homes are scanned too
        reg.scan(REPO / "suijin" / "modules")
        report = reg.resolve()
        assert not report.aborted
        assert any(u.id == "alpha" for u in report.boot_order)


class TestVendoredEstate:
    def test_vendored_packs_present_and_bootable(self):
        packs = [d for d in VENDORED.iterdir() if (d / "manifest.json").exists()]
        assert len(packs) >= 45, f"expected ~49 vendored packs, got {len(packs)}"
        for d in packs:
            assert (d / "plugin.json").exists(), f"{d.name}: plugin.json missing"
            assert (d / "entry.py").exists(), f"{d.name}: entry.py missing"

    def test_legacy_modules_tree_is_gone(self):
        assert not (REPO / "Modules").exists()
