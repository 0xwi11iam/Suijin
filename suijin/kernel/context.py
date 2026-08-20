"""Kernel Context — the syscall table. THE object handed to every module.

Config, workspace paths, the event bus, the tool registry, and named
services. Modules receive exactly one of these and touch the world
through nothing else. Stdlib only.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from suijin.kernel.audit import NullAudit, ToolAudit
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
        # every tool invocation is audit-logged (append-only JSONL under
        # <workspace>/outputs/audit_trails); controller.boot installs the
        # real sink, direct constructions get the inert default
        self.tool_audit: ToolAudit = NullAudit()

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
        self,
        name: str,
        fn: Callable[[dict, "Context"], str],
        description: str = "",
        owner: str = "",
        params: list[str] | None = None,
    ) -> None:
        """Register a callable(args, ctx) under a namespaced name.

        params: the tool's argument names (pack manifests declare them).
        Feeds tool_reference() so the kernel itself can render what the
        agent can call — the registry is the single source of truth.
        """
        self._tools[name] = {"fn": fn, "description": description, "owner": owner, "params": list(params or [])}

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def call_tool(self, name: str, args: dict) -> str:
        import time as _time

        entry = self._tools.get(name)
        if entry is None:
            self.tool_audit.record(surface="kernel", name=name, args=args, outcome="unknown-tool")
            return f"Error: unknown tool '{name}'"
        t0 = _time.perf_counter()
        try:
            out = str(entry["fn"](args or {}, self))
            outcome = "tool-error" if out.startswith("Error:") else "ok"
            self.tool_audit.record(
                surface="kernel",
                name=name,
                owner=entry.get("owner", ""),
                args=args,
                outcome=outcome,
                duration_ms=(_time.perf_counter() - t0) * 1000,
            )
            return out
        except Exception as e:  # noqa: BLE001 — tool failures are data
            logger.exception("tool %s failed", name)
            self.tool_audit.record(
                surface="kernel",
                name=name,
                owner=entry.get("owner", ""),
                args=args,
                outcome=f"exception:{type(e).__name__}",
                duration_ms=(_time.perf_counter() - t0) * 1000,
            )
            return f"Error: tool '{name}' failed: {e}"

    def tool_names(self, owner: str | None = None) -> list[str]:
        if owner is None:
            return sorted(self._tools)
        return sorted(n for n, e in self._tools.items() if e["owner"] == owner)

    def tool_reference(self, core_first: tuple[str, ...] = ()) -> str:
        """Render EVERY registered tool as the compact agent surface.

        One line per tool: name(arg, arg?) — description, grouped by
        owner. Rendered FROM the live registry: what is registered is
        what the agent sees — drift is impossible by construction.
        """
        by_owner: dict[str, list[tuple[str, list[str], str]]] = {}
        for name, entry in self._tools.items():
            by_owner.setdefault(entry["owner"] or "?", []).append(
                (name, entry.get("params") or [], entry.get("description") or "")
            )
        lines: list[str] = []

        # core-first ordering: the owner modules the agent uses daily
        def owner_key(owner: str) -> tuple[int, str]:
            for i, want in enumerate(core_first):
                if owner == want:
                    return (i, owner)
            return (len(core_first), owner)

        for owner in sorted(by_owner, key=owner_key):
            lines.append(f"[{owner}]")
            for name, params, desc in sorted(by_owner[owner]):
                args = ", ".join(params)
                sig = f"{name}({args})" if args else f"{name}()"
                one = " ".join(desc.split())  # collapse whitespace
                if len(one) > 90:
                    one = one[:87] + "..."
                lines.append(f"- {sig} — {one}")
        return "\n".join(lines)

    # ── events convenience ─────────────────────────────────────────

    def on_event(self, event: str, fn: Callable[[Any], None]) -> None:
        self.events.on(event, fn)

    def emit(self, event: str, payload: Any = None) -> None:
        self.events.emit(event, payload)
