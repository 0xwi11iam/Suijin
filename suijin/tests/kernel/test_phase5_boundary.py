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
    "cli.py": "console surface — the CLI may launch any surface (the console tier is the edge)",
}

# Legacy-path relocation shims: their whole job is re-exporting the real
# suijin.client.* / suijin.server.* package at the old import path, so a
# module-level import of the canonical target is the defining act, not a
# boundary violation. Keep in sync with the shims in modules/*/__init__.py.
_SPLIT_SHIMS = {
    # phase A — TUI moved to suijin/client/tui/
    "redteam/lib/red/console_ui.py",
    "redteam/lib/red/console_input.py",
    "redteam/lib/session_control.py",
    "redteam/lib/red/session_control.py",
    "redteam/lib/red/__init__.py",
    # phase B — server region relocated to suijin/server/
    "agent/__init__.py",
    "agent/graph/__init__.py",
    "platform/__init__.py",
    "providers/__init__.py",
    "tools/__init__.py",
    "ops/__init__.py",
    "redteam/__init__.py",
    "redteam/lib/__init__.py",
}


def _module_level_imports(tree: ast.Module, file: Path = None):
    """Imports at TOP level only (function bodies are lazy by definition).

    Relative imports resolve against the file's real package so an
    intra-module `from .json_utils import x` isn't misread as an
    suijin.modules.X reach-out."""
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level and file is not None:
                # resolve: this file's package, minus (level-1) hops, + base
                pkg_parts = file.parent.parts
                # walk up from the file's dir; level counts dots
                up = node.level - 1
                anchor = pkg_parts[: len(pkg_parts) - up] if up else pkg_parts
                base = ".".join(list(anchor) + (base.split(".") if base else []))
            elif node.level:
                base = f"suijin.modules.{base}" if base else "suijin.modules"
            yield base


def _own_namespace(py: Path) -> str:
    """The module-home namespace a lib/ file belongs to (e.g. suijin.modules.platform)."""
    parts = py.resolve().parts
    if "modules" in parts:
        i = parts.index("modules")
        if i + 1 < len(parts) and parts[i - 1] == "suijin":
            return "suijin.modules." + parts[i + 1]
    return ""


class TestModuleBoundaries:
    def test_modules_import_only_kernel_at_module_level(self):
        offenders = []
        for py in sorted(MODULES_DIR.rglob("*.py")):
            rel = str(py.relative_to(MODULES_DIR))
            if py.parent == MODULES_DIR and py.name == "__init__.py":
                continue  # package init (empty)
            if py.name in _ALLOWLIST:
                continue
            # Vendored third-party packs (manifest.json = pack marker):
            # externally authored units whose entry registration goes
            # through the kernel. The boundary contract governs FIRST-
            # PARTY module homes; packs are data-in/code-in bricks.
            if (py.parent / "manifest.json").exists():
                continue
            # SPLIT SHIMS (the client/server split): files whose
            # whole job is re-exporting suijin.client.* / suijin.server.*
            # at the legacy paths (phase A client + phase B server region)
            if rel.replace("\\", "/") in _SPLIT_SHIMS:
                continue
            tree = ast.parse(py.read_text(errors="ignore"))
            # lib/ = module-INTERNAL implementation: stdlib + third-party +
            # own-module imports allowed at module level (that's what a
            # module IS); only OTHER modules' namespaces are banned.
            own_ns = _own_namespace(py)
            is_lib = "/lib/" in rel.replace("\\", "/")
            for mod in _module_level_imports(tree, py):
                if mod.startswith("suijin.kernel") or mod == "suijin.kernel":
                    continue  # the kernel is the sanctioned coupling surface
                if mod.startswith("suijin"):
                    if is_lib and own_ns and mod.startswith(own_ns):
                        continue  # intra-module: fine
                    offenders.append(f"{rel}: module-level import {mod} (must be function-local)")
                elif not is_lib and mod.split(".")[0] not in sys.stdlib_module_names:
                    offenders.append(f"{rel}: module-level third-party import {mod}")
        assert not offenders, "boundary violations:\n" + "\n".join(offenders)

    def test_packbridge_seam_deleted(self):
        """The Phase-3 migration seam is gone — packs are self-contained."""
        assert not (MODULES_DIR / "_packbridge.py").exists()

    def test_no_module_imports_manager_internals(self):
        """manager (the API) and manager_tui (the surface) stay separable:
        nothing in modules/ imports manager_tui; the TUI imports manager."""
        for py in sorted(MODULES_DIR.rglob("*.py")):
            if py.name in ("manager_tui.py", "cli.py"):  # surfaces may launch surfaces
                continue
            src = py.read_text(errors="ignore")
            assert "manager_tui" not in src, f"{py.name} reaches into the TUI surface"
