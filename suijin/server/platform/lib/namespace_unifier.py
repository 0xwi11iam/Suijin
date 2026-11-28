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
import importlib.util
import sys


def _server_twin(server_name: str):
    """The spec of the server module with this name, or None when it has no twin.

    Never raises. importlib.util.find_spec RAISES ModuleNotFoundError when a parent is missing —
    a package that exists only in the modules tree (knowledge/, loader/, ...) has no server parent
    at all. A finder that raises breaks every import beneath such a package (2026-11-28: a
    rewrite that let this raise turned about 160 passing tests red in one run).
    """
    parent = server_name.rpartition(".")[0]
    if parent and parent not in sys.modules:
        try:
            importlib.import_module(parent)
        except ImportError:
            return None
    try:
        return importlib.util.find_spec(server_name)
    except (ImportError, AttributeError):  # no such parent, or a parent that is not a package
        return None


class _ModulesToServerFinder(importlib.abc.MetaPathFinder):
    """Redirects suijin.modules.* imports to suijin.server.* — the SAME
    module object, not a copy."""

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith("suijin.modules."):
            return None
        server_name = fullname.replace("suijin.modules.", "suijin.server.", 1)

        # What lies at this name along the search path we were handed?
        modules_spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        origin = str(getattr(modules_spec, "origin", None) or "") if modules_spec is not None else ""
        in_modules_tree = "suijin/modules" in origin
        if origin and in_modules_tree:
            if origin.endswith("__init__.py"):
                return None  # a real package of the modules tree — normal import
            # A real file in modules/. Modules like loader/ and console/ only exist there; others
            # are one-line shims whose real code is the server twin.
            server_spec = _server_twin(server_name)
            if server_spec is None:
                return None  # no server twin — let normal import handle it
            try:
                with open(origin, encoding="utf-8") as fh:
                    src = fh.read(500)
            except OSError:
                return None
            if "sys.modules" in src and ("_real" in src or "server" in src):
                return self._alias(fullname, server_name, server_spec)  # it's a shim file
            return None  # real code, not a shim — normal import

        # Nothing of the modules tree is at this name. Either there is no modules-path file at all,
        # or the spec was found through an ALIASED parent — whose __path__ is the SERVER tree, so
        # what PathFinder found IS the server twin (a package __init__, or a namespace package
        # with no origin at all). Returning None in either case let the normal import build a
        # second module object and execute the server code again under the modules name: the very
        # split this unifier exists to prevent, and the root of every order-dependent
        # "cannot import name X" failure. Alias it instead.
        server_spec = _server_twin(server_name)
        if server_spec is None:
            return None
        return self._alias(fullname, server_name, server_spec)

    @staticmethod
    def _alias(fullname: str, server_name: str, server_spec) -> importlib.machinery.ModuleSpec:
        """A spec that resolves `fullname` to the server module's own object.

        The twin is looked up with importlib.util.find_spec, never PathFinder alone: in an
        editable install (and inside a zipapp) the package is resolved by another finder, and a
        bare path lookup finds nothing — which silently disabled an earlier version of this.
        """
        return importlib.machinery.ModuleSpec(
            fullname,
            _ServerAliasLoader(server_name),
            origin=server_spec.origin,
            is_package=server_spec.submodule_search_locations is not None,
        )


class _ServerAliasLoader(importlib.abc.Loader):
    """Loads the server module, then registers it under the modules name
    — same object in sys.modules under both names."""

    def __init__(self, server_name: str):
        self._server_name = server_name
        self._server_spec = None

    def create_module(self, spec):
        # Import the server module (may already be loaded)
        server_mod = importlib.import_module(self._server_name)
        self._server_spec = server_mod.__spec__
        return server_mod  # THE SAME OBJECT

    def exec_module(self, module):
        # Nothing to execute — the server module is already running. But the import machinery
        # stamps the ALIAS spec, name and loader onto the object it was handed. Left there,
        # importlib.reload() takes its name from that spec, resolves the alias again and executes
        # nothing: a silent no-op for any module first imported through the modules name
        # (found 2026-11-28). One object keeps one identity — the server's own.
        spec = self._server_spec
        if spec is not None and getattr(module, "__spec__", None) is not spec:
            module.__spec__ = spec
            module.__name__ = spec.name
            module.__loader__ = spec.loader
            module.__package__ = spec.parent


def install():
    """Install the finder — called once from suijin/__init__.py."""
    # Don't double-install
    for finder in sys.meta_path:
        if isinstance(finder, _ModulesToServerFinder):
            return
    sys.meta_path.insert(0, _ModulesToServerFinder())
