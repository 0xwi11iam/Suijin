"""Kernel contracts — protocols and enums only. Stdlib only. Imports
NOTHING from suijin (enforced by test_kernel_purity).

The kernel understands CATEGORIES of software (Module, Tool, tier),
never specific modules — this is what lets arbitrary future software
snap in without kernel changes.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any, Protocol, runtime_checkable


class Tier(IntEnum):
    """Module tiers — ordered: lower value = more foundational."""

    CORE = 0  # system packages: boot aborts without them
    RECOMMENDED = 1  # bundled apps: disableable, shipped
    INSTALLED = 2  # community software: discovered at boot

    @classmethod
    def from_string(cls, value: str) -> "Tier":
        try:
            return cls[value.strip().upper()]
        except KeyError:
            raise ValueError(f"unknown tier '{value}' (core|recommended|installed)") from None


@runtime_checkable
class Tool(Protocol):
    """A callable registered on the Context by a module.

    name must be namespaced by module id ('nmap.scan'); permissions
    declares the capabilities the tool needs (network, shell, ...) so the
    security subsystem can gate them uniformly.
    """

    name: str
    description: str
    permissions: tuple[str, ...] = ()

    def __call__(self, args: dict, ctx: "Any") -> str: ...


@runtime_checkable
class Module(Protocol):
    """The unit of composition. One per plugin.json.

    register(ctx): declare services/tools/event subscriptions — CHEAP,
    no I/O, runs for every module before anything starts.
    start(ctx): bring the module live (may spawn threads, bind ports).
    stop(ctx): release resources. All three must be idempotent-safe.
    """

    id: str
    tier: Tier

    def register(self, ctx: Any) -> None: ...

    def start(self, ctx: Any) -> None: ...

    def stop(self, ctx: Any) -> None: ...
