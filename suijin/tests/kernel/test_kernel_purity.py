"""Kernel purity — the kernel imports NOTHING from suijin outside kernel/,
and nothing but the stdlib at all. This is the architectural keystone:
the piece that understands all software cannot depend on any of it.
"""

import ast
import subprocess
import sys
from pathlib import Path

KERNEL = Path(__file__).resolve().parents[2] / "suijin" / "kernel"

STDLIB_ALLOWLIST_PREFIXES = ("suijin.kernel.",)
# everything else suijin.* is banned; third-party is banned entirely


def _imports(tree: ast.Module) -> set[str]:
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    return mods


def test_kernel_imports_only_stdlib_and_kernel():
    offenders = []
    for py in sorted(KERNEL.glob("*.py")):
        if py.name == "__init__.py":
            continue
        tree = ast.parse(py.read_text())
        for mod in _imports(tree):
            if mod.startswith("suijin.kernel"):
                continue
            if mod.startswith("suijin"):
                offenders.append(f"{py.name} imports {mod}")
                continue
            # stdlib check via sys.stdlib_module_names
            root = mod.split(".")[0]
            if root and root not in sys.stdlib_module_names and root != "":
                offenders.append(f"{py.name} imports third-party {mod}")
    assert not offenders, "kernel purity violations:\n" + "\n".join(offenders)


def test_kernel_clean_interpreter_import():
    """Importing the whole kernel pulls zero suijin non-kernel SUBmodules.
    (sys.modules shows the root 'suijin' package __init__ — stdlib json +
    version.json read, which is fine; subpackages are the real leak.)"""
    code = (
        "import sys\n"
        "import suijin.kernel.controller, suijin.kernel.registry, "
        "suijin.kernel.context, suijin.kernel.events, suijin.kernel.contracts\n"
        "bad = [m for m in sys.modules if m.startswith('suijin.') "
        "and not m.startswith('suijin.kernel')]\n"
        "print('\\n'.join(bad))"
    )
    r = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[2])
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"kernel dragged in: {r.stdout.strip()}"
