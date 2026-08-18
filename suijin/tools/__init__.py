"""Tool implementations — dispatch, providers, web search.

DELIBERATELY EMPTY of eager imports (Phase 0, item 1): the old init
imported dispatch (the whole tool tree, providers, huggingface_hub,
module discovery, and a workspace migration) at package import time — so
even `from suijin.tools.workspace import WORKSPACE_DIR` executed
everything. Names are now resolved lazily via __getattr__; direct module
imports (`from suijin.tools.dispatch import route_tool`) are and always
were the preferred form.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # static analysis still sees the surface
    from suijin.tools.dispatch import (
        get_proxy as get_proxy,
        get_tool_catalog as get_tool_catalog,
        reset_recon_state as reset_recon_state,
        route_tool as route_tool,
        set_proxy as set_proxy,
    )
    from suijin.tools.providers import (
        generate as generate,
        get_usage as get_usage,
        reset_usage as reset_usage,
    )

_LAZY = {
    "get_proxy": ("suijin.tools.dispatch", "get_proxy"),
    "get_tool_catalog": ("suijin.tools.dispatch", "get_tool_catalog"),
    "reset_recon_state": ("suijin.tools.dispatch", "reset_recon_state"),
    "route_tool": ("suijin.tools.dispatch", "route_tool"),
    "set_proxy": ("suijin.tools.dispatch", "set_proxy"),
    "generate": ("suijin.tools.providers", "generate"),
    "get_usage": ("suijin.tools.providers", "get_usage"),
    "reset_usage": ("suijin.tools.providers", "reset_usage"),
}


def __getattr__(name: str):
    entry = _LAZY.get(name)
    import importlib

    if entry is not None:
        module = importlib.import_module(entry[0])
        return getattr(module, entry[1])
    # Submodule fallback (standard lazy-package pattern): makes
    # `suijin.tools.providers` / `monkeypatch.setattr("suijin.tools.x.y")`
    # resolve without eagerly importing anything.
    try:
        return importlib.import_module(f"{__name__}.{name}")
    except ImportError:
        raise AttributeError(f"module 'suijin.tools' has no attribute '{name}'") from None


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_LAZY.keys()))
