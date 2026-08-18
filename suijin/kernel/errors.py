"""Kernel error taxonomy — classifiable failures so the controller and
Module Manager can render them sensibly. Stdlib only.
"""

from __future__ import annotations


class BootError(RuntimeError):
    """The boot itself cannot proceed (core module missing/broken/cyclic)."""


class DependencyError(BootError):
    """A module's declared dependency cannot be satisfied."""


class PermissionDenied(RuntimeError):
    """A module attempted an operation its manifest does not declare."""


class QuarantinedModule(RuntimeError):
    """Operation on a module that was quarantined at boot."""
