"""Kernel security — the module permission model.

Modules DECLARE capabilities in plugin.json (permissions: [...]).
The kernel knows the full vocabulary and enforces at one point; the
Module Manager renders what each module asked for. Stdlib only.
"""

from __future__ import annotations

KNOWN_PERMISSIONS = frozenset(
    {
        "network",  # outbound HTTP/TCP to targets
        "shell",  # subprocess execution
        "filesystem",  # writes via the VFS
        "provider",  # access to LLM services on the Context
        "events.listen",  # subscribe to the event bus
        "events.emit",  # publish events
    }
)


class PermissionSet:
    """Validated permission declaration for one module."""

    def __init__(self, perms: frozenset[str]) -> None:
        self._perms = perms

    @classmethod
    def from_manifest(cls, declared: list[str]) -> "PermissionSet":
        unknown = [p for p in (declared or []) if p not in KNOWN_PERMISSIONS]
        if unknown:
            raise ValueError(
                f"unknown permission(s): {', '.join(unknown)} (known: {', '.join(sorted(KNOWN_PERMISSIONS))})"
            )
        return cls(frozenset(declared or []))

    def has(self, perm: str) -> bool:
        return perm in self._perms

    @property
    def all(self) -> frozenset[str]:
        return self._perms


def enforce(perms: PermissionSet, needed: str, context: str) -> str | None:
    """Return a denial message when `needed` isn't declared, else None."""
    if perms.has(needed):
        return None
    return (
        f"permission denied: '{context}' requires '{needed}' but the module "
        f"declared only [{', '.join(sorted(perms.all)) or 'none'}]"
    )
