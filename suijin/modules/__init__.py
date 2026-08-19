"""Suijin OS module homes + the legacy pack loader.

v4.1: LAZY — importing suijin.modules.<anything> must not execute the
pack loader (module discovery, runtime init) as an import side effect.
The loader's names resolve on first attribute access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # static-analysis surface
    from suijin.modules.loader import (
        discover_modules as discover_modules,
    )
    from suijin.modules.loader import (
        get_loaded_modules as get_loaded_modules,
    )
    from suijin.modules.loader import (
        get_module_skills as get_module_skills,
    )
    from suijin.modules.loader import (
        get_module_tools as get_module_tools,
    )
    from suijin.modules.loader import (
        load_local_module as load_local_module,
    )

_LAZY = {
    "discover_modules": ("suijin.modules.loader", "discover_modules"),
    "get_loaded_modules": ("suijin.modules.loader", "get_loaded_modules"),
    "get_module_skills": ("suijin.modules.loader", "get_module_skills"),
    "get_module_tools": ("suijin.modules.loader", "get_module_tools"),
    "load_local_module": ("suijin.modules.loader", "load_local_module"),
}


def __getattr__(name: str):
    entry = _LAZY.get(name)
    if entry is None:
        raise AttributeError(f"module 'suijin.modules' has no attribute '{name}'")
    import importlib

    return getattr(importlib.import_module(entry[0]), entry[1])


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_LAZY.keys()))
