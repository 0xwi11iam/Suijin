"""Import-graph guard — every suijin.* import in live code must resolve to
a file that exists. Prevents 'deleted a module, broke an import' regressions
(the exact failure class this dead-code sweep guards against).
"""

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "suijin"
EXCLUDE = {"tests", "lab", "__pycache__", "kb_cache", "ui"}


def _live_files():
    return [p for p in PKG.rglob("*.py") if not any(part in EXCLUDE for part in p.parts)]


def _module_targets(m: str) -> set[Path]:
    """File paths a module string can refer to."""
    rel = m.replace(".", "/")
    return {PKG.parent / (rel + ".py"), PKG.parent / rel / "__init__.py"}


def _collect_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(errors="ignore"))
    mods = set()
    rel_parts = list(path.relative_to(PKG.parent).with_suffix("").parts)
    is_init = rel_parts[-1] == "__init__"
    if is_init:
        rel_parts = rel_parts[:-1]
    me = ".".join(rel_parts)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                # level 1 = current package; each extra level goes up one more.
                # A plain module's package is me-minus-1; an __init__ IS me.
                drop = node.level if not is_init else node.level - 1
                pkg_parts = me.split(".")[: len(me.split(".")) - drop] if drop else me.split(".")
                pkg = ".".join(pkg_parts)
                base = f"{pkg}.{base}" if base else pkg
            if base:
                mods.add(base)
    return mods


def test_no_dangling_suijin_imports():
    dangling = []
    for f in _live_files():
        for m in _collect_imports(f):
            if not m.startswith("suijin"):
                continue
            if m == "suijin_core":
                continue  # the compiled core: optional build artifact, not a source file
            targets = _module_targets(m)
            if not any(t.exists() for t in targets):
                dangling.append(f"{f.relative_to(REPO)} imports missing {m}")
    assert not dangling, "dangling imports:\n" + "\n".join(dangling)


def test_entry_points_importable():
    import importlib

    for mod in ("suijin.main", "suijin.cli", "suijin.modules.knowledge.lib.kb", "suijin.ui.server"):
        importlib.import_module(mod)


def test_blue_tree_only_contains_live_modules():
    """The packages pruned in v2.11.2 stay pruned."""
    gone = [
        "counter_intel",
        "endpoints",
        "forensics",
        "hotfix",
        "intel",
        "response",
        "orchestrator.py",
    ]
    for g in gone:
        assert not (PKG / "core" / "blue" / g).exists(), g


@pytest.mark.parametrize(
    "module",
    [
        "suijin.modules.blueteam.lib.blue.traffic.anomaly_detector",
        "suijin.modules.blueteam.lib.blue.traffic.scorer",
        "suijin.modules.blueteam.lib.blue.traffic.replay_harness",
        "suijin.modules.blueteam.lib.blue.defense.firewall",
        "suijin.modules.blueteam.lib.blue.ai_engine",
        "suijin.modules.blueteam.lib.blue.knowledge_graph",
        "suijin.modules.blueteam.lib.blue.subagent_manager",
        "suijin.modules.agent.lib.prompts.blue_system",
    ],
)
def test_kept_blue_modules_importable(module):
    import importlib

    importlib.import_module(module)
