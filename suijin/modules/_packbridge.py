"""Pack bridge — load one legacy pack's tool callables by directory name.

The legacy pack loader (suijin/modules/loader.py) cannot be imported as
`suijin.modules.loader` now that suijin/modules is a package (the OS
module tree). This bridge locates it by FILE and calls its force-load
helpers directly — one isolated seam, deleted when packs are fully
migrated in Phase 5.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_LOADER_PATH = Path(__file__).resolve().parent / "loader.py"
_cache: dict[str, object] = {}


def _loader():
    if "suijin._legacy_pack_loader" not in sys.modules:
        spec = importlib.util.spec_from_file_location("suijin._legacy_pack_loader", _LOADER_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["suijin._legacy_pack_loader"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["suijin._legacy_pack_loader"]


def load_pack_tools(pack_dir_name: str) -> dict:
    """{tool_name: callable} for one pack (by its Modules/<cat>/<name> dir)."""
    key = pack_dir_name
    if key in _cache:
        return _cache[key]
    loader = _loader()
    loader.discover_modules()
    all_tools = loader.get_module_tools()
    # map tool -> owning pack via the loaded module registry
    out = {}
    loaded = loader.get_loaded_modules() or {}
    for mod_key, mod_data in loaded.items():
        if mod_key.split("/")[-1] != pack_dir_name:
            continue
        for tool_name in mod_data.get("manifest", {}).get("tools") or {}:
            fn = all_tools.get(tool_name)
            if fn is not None:
                out[tool_name] = fn
    _cache[key] = out
    return out


def all_pack_tool_names() -> set[str]:
    """Tool names genuinely DECLARED BY PACKS (from pack manifests only).

    The legacy loader's get_module_tools() mixes in core builtins, so
    this walks get_loaded_modules() — pack manifests only — collecting
    their declared tool names. Core builtins never appear here.
    """
    loader = _loader()
    if not loader.get_loaded_modules():
        loader.discover_modules()
    names: set[str] = set()
    for mod_data in (loader.get_loaded_modules() or {}).values():
        names.update((mod_data.get("manifest", {}).get("tools") or {}).keys())
    return names


def packs_booted(ctx) -> bool:
    """True when any pack module actually booted in this Context."""
    try:
        booted = getattr(ctx, "_booted_unit_ids", None)
        if booted is None:
            return False
        return bool(booted & _known_pack_ids())
    except Exception:
        return False


def _known_pack_ids() -> set[str]:
    loader = _loader()
    if not loader.get_loaded_modules():
        loader.discover_modules()
    return {m.split("/")[-1].lower() for m in (loader.get_loaded_modules() or {})}
