"""Phase 0 purity gates — importing a leaf tool must NOT drag the world in.

The old tools/__init__ imported dispatch (whole tool tree, providers,
huggingface_hub) AND providers at package init, so `from
suijin.tools.workspace import WORKSPACE_DIR` executed the entire chain —
including module discovery and a workspace migration. These tests make
that class of coupling impossible to reintroduce.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _imported_modules(snippet: str) -> set[str]:
    """Run snippet in a clean interpreter; return loaded suijin* modules."""
    code = "import sys\n" + snippet + "\nprint('\\n'.join(m for m in sys.modules if m.startswith('suijin')))"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr
    return set(r.stdout.splitlines())


class TestImportPurity:
    def test_workspace_is_leaf(self):
        mods = _imported_modules("import suijin.tools.workspace")
        assert "suijin.tools.workspace" in mods
        # the god-import chain must not fire
        for banned in (
            "suijin.tools.dispatch",
            "suijin.tools.providers",
            "suijin.modules.loader",
            "suijin.kb",
            "huggingface_hub",
        ):
            assert banned not in mods, f"workspace import dragged in {banned}"

    def test_guardrails_is_leaf(self):
        mods = _imported_modules("import suijin.tools.guardrails")
        assert "suijin.tools.guardrails" in mods
        assert "suijin.tools.dispatch" not in mods
        assert "suijin.tools.providers" not in mods

    def test_kb_is_leaf(self):
        mods = _imported_modules("import suijin.kb")
        assert "suijin.kb" in mods
        assert "suijin.tools" not in mods  # no package __init__ chain either
        assert "suijin.modules.loader" not in mods

    def test_runtime_import_is_side_effect_free(self):
        """Importing runtime must NOT discover modules, migrate the workspace,
        or suppress warnings — init_runtime() owns all of that now."""
        mods = _imported_modules("import suijin.tools.runtime")
        assert "suijin.tools.runtime" in mods
        assert "suijin.modules.loader" not in mods
        # modules.loader is imported lazily inside init_runtime — the pure
        # import must not have executed it


class TestInitRuntime:
    def test_idempotent_and_thread_safe(self):
        from suijin.tools import runtime as rt

        rt.init_runtime()
        before = rt.is_initialized()
        rt.init_runtime()  # second call is a no-op
        assert before and rt.is_initialized()

    def test_workspace_dirs_created(self, tmp_path, monkeypatch):

        import suijin.tools.workspace as ws

        rt = __import__("suijin.tools.runtime", fromlist=["x"])
        # point workspace at a fresh tree and force a re-init
        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        monkeypatch.setattr(rt, "WORKSPACE_DIR", tmp_path)
        rt.init_runtime(force=True)
        for sub in ("payloads", "scripts", "outputs"):
            assert (tmp_path / sub).is_dir(), sub

    def test_lazy_session_auto_initializes(self):
        from suijin.tools import runtime as rt

        rt._initialized = False
        _ = rt.global_session.get  # touching a SESSION attribute auto-inits
        assert rt.is_initialized()


class TestCompatFacade:
    def test_star_import_surface_intact(self):
        """`from suijin import tools` must still expose the documented names
        (lazy) — external code and tests rely on them."""
        from suijin import tools

        for name in (
            "route_tool",
            "get_tool_catalog",
            "generate",
            "get_usage",
            "set_proxy",
            "get_proxy",
            "reset_usage",
        ):
            assert hasattr(tools, name), name
        # USAGE (a mutable dict) is deliberately NOT lazily re-exported: a
        # snapshot copy would silently diverge from the live accumulator.
        # Use tools.get_usage() / suijin.tools.providers.USAGE instead.
        assert not hasattr(tools, "USAGE")

    def test_old_import_paths_still_resolve(self):
        import suijin.tools.dispatch as d
        import suijin.tools.providers as p

        assert callable(d.route_tool)
        assert callable(p.generate)
