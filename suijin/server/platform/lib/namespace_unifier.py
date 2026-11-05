"""The namespace unifier — one module object per file, forever (2026-11-05).

THE DUAL-NAMESPACE BUG: suijin/modules/* shims point to suijin/server/*,
but Python creates SEPARATE module objects for submodules imported
through each path. Same file, two objects, duplicated state — the root
cause of the order-dependent test flakes and every "I patched one but
the other is live" bug in this codebase's history.

THE FIX: a meta-path finder that resolves `suijin.modules.X.Y.Z` to the
ALREADY-LOADED `suijin.server.X.Y.Z` module object. One file, one
object, no duplicates. Imported once from suijin/__init__.py.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys


class _ModulesToServerFinder(importlib.abc.MetaPathFinder):
    """Redirects suijin.modules.* imports to suijin.server.* — the SAME
    module object, not a copy."""

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith("suijin.modules."):
            return None
        # Only redirect when the modules path is a SHIM (the real file
        # lives under server/). If the module file actually exists at
        # suijin/modules/... let the normal import handle it — modules
        # like loader/ console only exist in the modules tree.
        server_name = fullname.replace("suijin.modules.", "suijin.server.", 1)

        # Check: does the modules-path file exist as real code?
        modules_spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if modules_spec is not None and modules_spec.origin:
            # The file exists at modules/ — check if it's a shim (contains
            # the redirect pattern) or real code
            origin = str(modules_spec.origin)
            if "suijin/modules" in origin and not origin.endswith("__init__.py"):
                # It's a real file in modules/ — is there a server twin?
                server_spec = importlib.machinery.PathFinder.find_spec(server_name)
                if server_spec is None:
                    return None  # no server twin — let normal import handle it
                # Both exist — check if the modules file is just a shim
                try:
                    with open(origin, encoding="utf-8") as fh:
                        src = fh.read(500)
                    if "sys.modules" in src and ("_real" in src or "server" in src):
                        # It's a shim file — redirect to the server twin
                        return importlib.machinery.ModuleSpec(
                            fullname, _ServerAliasLoader(server_name), origin=server_spec.origin
                        )
                except OSError:
                    pass
            return None  # real file, not a shim — normal import
        # No modules-path file — check if the server twin exists
        server_spec = importlib.machinery.PathFinder.find_spec(server_name)
        if server_spec is None:
            return None
        return importlib.machinery.ModuleSpec(
            fullname, _ServerAliasLoader(server_name), origin=server_spec.origin
        )


class _ServerAliasLoader(importlib.abc.Loader):
    """Loads the server module, then registers it under the modules name
    — same object in sys.modules under both names."""

    def __init__(self, server_name: str):
        self._server_name = server_name

    def create_module(self, spec):
        # Import the server module (may already be loaded)
        server_mod = importlib.import_module(self._server_name)
        return server_mod  # THE SAME OBJECT

    def exec_module(self, module):
        pass  # already executed — we're just aliasing


def install():
    """Install the finder — called once from suijin/__init__.py."""
    # Don't double-install
    for finder in sys.meta_path:
        if isinstance(finder, _ModulesToServerFinder):
            return
    sys.meta_path.insert(0, _ModulesToServerFinder())
