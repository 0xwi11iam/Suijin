"""DEPRECATED (v4.1 modularisation): lives at suijin.modules.platform.lib.agent_context. Lazy shim."""

import importlib as _il

from suijin.modules.platform.lib.agent_context import *  # noqa: F401,F403 — re-export public names

_target = _il.import_module("suijin.modules.platform.lib.agent_context")
__all__ = [n for n in dir(_target) if not n.startswith("__")]


def __getattr__(name):
    return getattr(_target, name)


def __dir__():
    return dir(_target)
