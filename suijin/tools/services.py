"""DEPRECATED (v4.1 modularisation): lives at suijin.modules.tools.lib.services. Lazy shim.

Pure-delegation: every attribute read resolves against the canonical
module at ACCESS time, so monkeypatch.setattr on either module is
visible through both."""

import importlib as _il

_target = _il.import_module("suijin.modules.tools.lib.services")
__all__ = [x for x in dir(_target) if not x.startswith("__")]


def __getattr__(name):
    return getattr(_target, name)


def __dir__():
    return dir(_target)
