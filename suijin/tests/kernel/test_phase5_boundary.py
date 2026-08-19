"""Phase 5 — boundary linter: modules couple ONLY through the kernel.

Enforced rule: within suijin/modules/**, module-level imports may be
stdlib or suijin.kernel.* — everything else must be resolved lazily
inside functions (i.e., at boot-time through the Context). This is the
structural rule that makes modules snap-in/snap-out: a module that
imports another subsystem at import time is welded to it, not snapped.

Sanctioned exception: manager_tui.py (the Textual console surface — the
console tier is the edge and may own UI toolkits).
"""

import ast
import sys
from pathlib import Path

MODULES_DIR = Path(__file__).resolve().parents[2] / "modules"

# Files exempt from the third-party/module-level rule, with reasons.
_ALLOWLIST = {
    "manager_tui.py": "console surface — Textual is its toolkit (the console tier is the edge)",
}


def _module_level_imports(tree: ast.Module):
    """Imports at TOP level only (function bodies are lazy by definition)."""
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:  # relative import at module level
                base = f"suijin.modules.{base}" if base else "suijin.modules"
            yield base


class TestModuleBoundaries:
    def test_modules_import_only_kernel_at_module_level(self):
        offenders = []
        for py in sorted(MODULES_DIR.rglob("*.py")):
            rel = str(py.relative_to(MODULES_DIR))
            if py.parent == MODULES_DIR and py.name == "__init__.py":
                continue  # package init (empty)
            if py.name in _ALLOWLIST:
                continue
            tree = ast.parse(py.read_text(errors="ignore"))
            for mod in _module_level_imports(tree):
                if mod.startswith("suijin.kernel") or mod == "suijin.kernel":
                    continue  # the kernel is the sanctioned coupling surface
                if mod.startswith("suijin"):
                    offenders.append(f"{rel}: module-level import {mod} (must be function-local)")
                elif mod.split(".")[0] not in sys.stdlib_module_names:
                    offenders.append(f"{rel}: module-level third-party import {mod}")
        assert not offenders, "boundary violations:\n" + "\n".join(offenders)

    def test_packbridge_seam_deleted(self):
        """The Phase-3 migration seam is gone — packs are self-contained."""
        assert not (MODULES_DIR / "_packbridge.py").exists()

    def test_no_module_imports_manager_internals(self):
        """manager (the API) and manager_tui (the surface) stay separable:
        nothing in modules/ imports manager_tui; the TUI imports manager."""
        for py in sorted(MODULES_DIR.rglob("*.py")):
            if py.name == "manager_tui.py":
                continue
            src = py.read_text(errors="ignore")
            assert "manager_tui" not in src, f"{py.name} reaches into the TUI surface"
