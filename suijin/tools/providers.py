"""DEPRECATED (v4.1 modularisation): lives at suijin.modules.providers.lib. Lazy shim."""

import importlib as _il

from suijin.modules.providers.lib import *  # noqa: F401,F403

_target = _il.import_module("suijin.modules.providers.lib")
__all__ = [n for n in dir(_target) if not n.startswith("__")]


def __getattr__(name):
    return getattr(_target, name)


def __dir__():
    return dir(_target)
