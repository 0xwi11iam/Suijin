"""Phase 0 purity gates — importing a leaf tool must NOT drag the world in.

The old package init imported dispatch (whole tool tree, providers,
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
        mods = _imported_modules("import suijin.modules.platform.lib.workspace")
        assert "suijin.modules.platform.lib.workspace" in mods
        # the god-import chain must not fire
        for banned in (
            "suijin.modules.tools.lib.dispatch",
            "suijin.modules.providers.lib",
            "suijin.modules.loader",
            "suijin.modules.knowledge.lib.kb",
            "huggingface_hub",
        ):
            assert banned not in mods, f"workspace import dragged in {banned}"

    def test_guardrails_is_leaf(self):
        mods = _imported_modules("import suijin.modules.tools.lib.guardrails")
        assert "suijin.modules.tools.lib.guardrails" in mods
        assert "suijin.modules.tools.lib.dispatch" not in mods
        assert "suijin.modules.providers.lib" not in mods

    def test_kb_is_leaf(self):
        mods = _imported_modules("import suijin.modules.knowledge.lib.kb")
        assert "suijin.modules.knowledge.lib.kb" in mods
        assert "suijin.modules.tools.lib.dispatch" not in mods
        assert "suijin.modules.loader" not in mods

    def test_runtime_import_is_side_effect_free(self):
        """Importing runtime must NOT discover modules, migrate the workspace,
        or suppress warnings — init_runtime() owns all of that now."""
        mods = _imported_modules("import suijin.modules.platform.lib.runtime")
        assert "suijin.modules.platform.lib.runtime" in mods
        assert "suijin.modules.loader" not in mods
        # modules.loader is imported lazily inside init_runtime — the pure
        # import must not have executed it


class TestInitRuntime:
    def test_idempotent_and_thread_safe(self):
        from suijin.modules.platform.lib import runtime as rt

        rt.init_runtime()
        before = rt.is_initialized()
        rt.init_runtime()  # second call is a no-op
        assert before and rt.is_initialized()

    def test_workspace_dirs_created(self, tmp_path, monkeypatch):

        import suijin.modules.platform.lib.workspace as ws

        rt = __import__("suijin.modules.platform.lib.runtime", fromlist=["x"])
        # point workspace at a fresh tree and force a re-init
        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        monkeypatch.setattr(rt, "WORKSPACE_DIR", tmp_path)
        rt.init_runtime(force=True)
        for sub in ("payloads", "scripts", "outputs"):
            assert (tmp_path / sub).is_dir(), sub

    def test_lazy_session_auto_initializes(self):
        from suijin.modules.platform.lib import runtime as rt

        rt._initialized = False
        _ = rt.global_session.get  # touching a SESSION attribute auto-inits
        assert rt.is_initialized()


class TestCanonicalPaths:
    def test_old_import_paths_still_resolve(self):
        import suijin.modules.providers.lib as p
        import suijin.modules.tools.lib.dispatch as d

        assert callable(d.route_tool)
        assert callable(p.generate)
