"""Native shim — the ONLY file that may touch the compiled core.

Tries, in order:
  1. the installed wheel (import suijin_core)
  2. a dev build (native/suijin-core/target/release), for hack sessions
and falls back to the pure-Python oracle when neither exists. Callers
get identical behavior either way — the oracle tests prove it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_pure = importlib.import_module("suijin.kernel._pure")

_native = None
_native_source = ""


def _try_load():
    global _native, _native_source
    # 1. installed wheel
    if "suijin_core" in sys.modules:
        _native, _native_source = sys.modules["suijin_core"], "wheel(cached)"
        return
    try:
        import suijin_core  # type: ignore

        _native, _native_source = suijin_core, "wheel"
        return
    except ImportError:
        pass
    # 2. dev cdylib next to the crate (PyO3 exports PyInit_suijin_core,
    #    so the module MUST be loaded under that exact name)
    here = Path(__file__).resolve().parents[2] / "native" / "suijin-core" / "target" / "release"
    for candidate in sorted(here.glob("libsuijin_core.*")):
        if candidate.suffix not in (".dylib", ".so"):
            continue
        ext = candidate.with_name("suijin_core.abi3.so")
        # freshness check: cargo rebuilds produce a NEWER dylib; a stale
        # copy would keep loading the old binary after every rebuild
        if not ext.exists() or candidate.stat().st_mtime > ext.stat().st_mtime:
            import shutil

            shutil.copy2(candidate, ext)
        spec = importlib.util.spec_from_file_location("suijin_core", ext)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules["suijin_core"] = mod
        _native, _native_source = mod, "dev-build"
        return


_try_load()


def available() -> bool:
    return _native is not None


def source() -> str:
    return _native_source if _native is not None else "pure-python"


def resolve_dag(manifests_json: str) -> str:
    if _native is not None:
        return _native.resolve_dag(manifests_json)
    return _pure.resolve_dag(manifests_json)


def check_paths(paths_json: str) -> str:
    if _native is not None:
        return _native.check_paths(paths_json)
    return _pure.check_paths(paths_json)
