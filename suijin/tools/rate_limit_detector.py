"""DEPRECATED (v4.1 modularisation): lives at suijin.modules.tools.lib.rate_limit_detector. Lazy shim.

Pure-delegation: every attribute read resolves against the canonical
module at ACCESS time, so monkeypatch.setattr on either module is
visible through both (the star-import snapshot was patch-blind)."""

import importlib as _il

_target = _il.import_module("suijin.modules.tools.lib.rate_limit_detector")
__all__ = [n for n in dir(_target) if not n.startswith("__")]


def __getattr__(name):
    return getattr(_target, name)


def __dir__():
    return dir(_target)
