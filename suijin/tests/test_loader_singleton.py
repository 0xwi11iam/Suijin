"""Phase 0, item 3 — load_local_module must return ONE instance per file.

The old loader re-executed the target file on every call WITHOUT caching in
sys.modules, so providers.py ran as five separate module objects (one per
call site), each with a private USAGE cost accumulator — the split-brain
the recon confirmed. These tests pin the fixed contract.
"""

import sys


class TestSingleInstance:
    def test_same_module_returned_every_call(self):
        from suijin.modules.loader import load_local_module

        a = load_local_module("providers")
        b = load_local_module("providers")
        assert a is b, "loader must return the cached instance, not re-execute"

    def test_instance_matches_normal_import(self):
        """Dynamic load and normal import must be the SAME object — cost
        accumulators and monkeypatches shared everywhere."""
        from suijin.modules.loader import load_local_module
        from suijin.tools import providers

        assert load_local_module("providers") is providers

    def test_usage_accumulator_shared(self):
        """The concrete symptom: USAGE incremented via one handle is visible
        through the other (was impossible with 5 instances)."""
        from suijin.modules.loader import load_local_module
        from suijin.tools import providers

        dyn = load_local_module("providers")
        before = dyn.USAGE["calls"]
        providers.USAGE["calls"] += 1
        assert dyn.USAGE["calls"] == before + 1

    def test_cached_in_sys_modules(self):
        from suijin.modules.loader import load_local_module

        mod = load_local_module("providers")
        assert any(v is mod for v in sys.modules.values() if v is not None)


class TestLoaderRobustness:
    def test_missing_module_raises_clearly(self, monkeypatch):
        """fugu.py:455 loads 'tools' which never existed — the failure must
        be a clear ModuleNotFoundError, not an opaque FileNotFoundError."""
        import pytest

        from suijin.modules import loader

        monkeypatch.setattr(loader, "BASE_DIR", loader.Path("/nonexistent"))
        with pytest.raises(ModuleNotFoundError, match="suijin module 'nope'"):
            loader.load_local_module("nope")

    def test_search_paths_are_deterministic(self):
        from suijin.modules import loader

        assert loader.SEARCH_DIRS[0].name == "suijin"  # package root first
