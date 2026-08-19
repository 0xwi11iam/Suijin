"""DEPRECATED (v4.1 modularisation): lives at suijin.modules.platform.lib.infra.workspace_fs. Lazy shim."""

import importlib as _il

_target = _il.import_module("suijin.modules.platform.lib.infra.workspace_fs")
__all__ = [x for x in dir(_target) if not x.startswith("__")]


def __getattr__(name):
    return getattr(_target, name)


def __dir__():
    return dir(_target)
