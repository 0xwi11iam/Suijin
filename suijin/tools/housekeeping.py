"""DEPRECATED (v4.1 modularisation): lives at suijin.modules.ops.lib.housekeeping. Lazy shim."""

import importlib as _il

from suijin.modules.ops.lib.housekeeping import *  # noqa: F401,F403

_target = _il.import_module("suijin.modules.ops.lib.housekeeping")
__all__ = [n for n in dir(_target) if not n.startswith("__")]


def __getattr__(name):
    return getattr(_target, name)


def __dir__():
    return dir(_target)
