"""Phase 0, item 5 — the tools module must not import suijin.core (inverted deps).

The recon found 8 sites where tool modules reached UP into core (blue
scorer, red session_control, red config_loader) — the exact shape the
kernel forbids. The seam: tools/services.py, a stdlib service locator
where core registers its capabilities at boot. Tools import only the
seam. This test FAILS on any reintroduced inversion.
"""

import ast
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "modules" / "tools" / "lib"

# Importing core.CONSTANTS from tools is acceptable (shared kernel
# values, no behavior); importing anything else under suijin.core is not.
_BANNED = "suijin.core"
_ALLOWED_PREFIX = "suijin.modules.platform.lib.constants"
_SEAM = "modules/tools/lib/services.py"


def _imports(tree: ast.Module) -> set[tuple[int, str]]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add((node.lineno, a.name))
        elif isinstance(node, ast.ImportFrom):
            base = f"{node.module}" if node.module else ""
            if node.level:  # relative — resolve against the tools lib package
                base = f"suijin.modules.tools.lib.{base}" if base else "suijin.modules.tools.lib"
            out.add((node.lineno, base))
    return out


def test_no_tools_to_core_inversions():
    """No file under tools/ (except the sanctioned seam) may import
    suijin.core beyond .constants."""
    offenders = []
    for py in sorted(TOOLS_DIR.glob("*.py")):
        if py.name == "services.py":
            continue
        tree = ast.parse(py.read_text(errors="ignore"))
        for lineno, mod in _imports(tree):
            if mod.startswith(_BANNED) and not mod.startswith(_ALLOWED_PREFIX):
                offenders.append(f"{py.name}:{lineno} imports {mod}")
    assert not offenders, "tools→core inversions:\n" + "\n".join(offenders)


class TestServiceSeam:
    def test_register_and_get(self):
        from suijin.modules.tools.lib import services

        def make():
            return {"kind": "scorer"}

        services.register("traffic_scorer", make)
        assert services.get("traffic_scorer")["kind"] == "scorer"

    def test_get_missing_returns_none(self):
        from suijin.modules.tools.lib import services

        assert services.get("definitely_not_registered_xyz") is None

    def test_last_registration_wins(self):
        from suijin.modules.tools.lib import services

        services.register("seam_test", lambda: 1)
        services.register("seam_test", lambda: 2)
        assert services.get("seam_test") == 2

    def test_producers_lazy(self):
        """Producers must be callables invoked at GET time — nothing should
        execute at registration (kernel rule: no import side effects)."""
        from suijin.modules.tools.lib import services

        calls = []
        services.register("seam_lazy", lambda: calls.append(1) or "made")
        assert calls == []  # not called yet
        assert services.get("seam_lazy") == "made"
        assert calls == [1]

    def test_core_services_registered_at_runtime_init(self):
        from suijin.modules.tools.lib import services

        if not services.has("traffic_scorer"):
            import pytest

            pytest.skip("runtime not initialized in this interpreter")
        assert callable(services.producer("traffic_scorer"))
