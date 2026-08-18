"""Kernel Context — the syscall table. THE object handed to every module.

Config, workspace paths, the event bus, the tool registry, and named
services. Modules receive exactly one of these and touch the world
through nothing else. Stdlib only.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from suijin.kernel.events import EventBus

logger = logging.getLogger("suijin.kernel.context")


class Context:
    """Per-boot context. Created by controller.boot(); read-only-ish by
    convention (modules may register; nothing mutates config at runtime)."""

    def __init__(
        self, config: dict | None = None, workspace: str | Path | None = None, events: EventBus | None = None
    ) -> None:
        self.config: dict = config or {}
        self.workspace: Path = Path(workspace) if workspace else Path.cwd()
        self.events: EventBus = events or EventBus()
        self._services: dict[str, Callable[[], Any]] = {}
        self._service_cache: dict[str, Any] = {}
        self._tools: dict[str, dict] = {}  # name -> {fn, description, owner}

    # ── services (lazy singletons) ─────────────────────────────────

    def register_service(self, name: str, producer: Callable[[], Any]) -> None:
        if not callable(producer):
            raise TypeError(f"service producer for '{name}' must be callable")
        self._services[name] = producer
        self._service_cache.pop(name, None)

    def service(self, name: str) -> Any:
        """Materialize once; None when never registered."""
        if name in self._service_cache:
            return self._service_cache[name]
        producer = self._services.get(name)
        if producer is None:
            return None
        value = producer()
        self._service_cache[name] = value
        return value

    def has_service(self, name: str) -> bool:
        return name in self._services

    # ── tools ──────────────────────────────────────────────────────

    def register_tool(
        self, name: str, fn: Callable[[dict, "Context"], str], description: str = "", owner: str = ""
    ) -> None:
        """Register a callable(args, ctx) under a namespaced name."""
        self._tools[name] = {"fn": fn, "description": description, "owner": owner}

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def call_tool(self, name: str, args: dict) -> str:
        entry = self._tools.get(name)
        if entry is None:
            return f"Error: unknown tool '{name}'"
        try:
            return str(entry["fn"](args or {}, self))
        except Exception as e:  # noqa: BLE001 — tool failures are data
            logger.exception("tool %s failed", name)
            return f"Error: tool '{name}' failed: {e}"

    def tool_names(self, owner: str | None = None) -> list[str]:
        if owner is None:
            return sorted(self._tools)
        return sorted(n for n, e in self._tools.items() if e["owner"] == owner)

    # ── events convenience ─────────────────────────────────────────

    def on_event(self, event: str, fn: Callable[[Any], None]) -> None:
        self.events.on(event, fn)

    def emit(self, event: str, payload: Any = None) -> None:
        self.events.emit(event, payload)
