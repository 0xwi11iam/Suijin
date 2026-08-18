"""Service seam — the proto-Context (Phase 0, item 5).

Tool modules must never import suijin.core upward (the recon found 8 such
inversions: battle/housekeeping → blue scorer, run_commands → red
session_control, providers → red config_loader). Instead, core registers
its capabilities here at runtime-init, and tools import ONLY this seam.

This is deliberately tiny: in Phase 1 it becomes kernel Context.services.
Contract:
  - register(name, producer): producer is a ZERO-ARG callable invoked at
    get() time — registration never executes anything (no import side
    effects, no cycles)
  - get(name) → value or None (missing services are the caller's problem
    to degrade gracefully)
  - has(name) / producer(name) for introspection and tests

Stdlib only. Thread-safe.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_producers: dict[str, object] = {}
_cache: dict[str, object] = {}


def register(name: str, producer) -> None:
    """Register (or replace) a producer for a named service."""
    if not callable(producer):
        raise TypeError(f"service producer for '{name}' must be callable")
    with _lock:
        _producers[name] = producer
        _cache.pop(name, None)  # invalidate cached value on replace


def get(name: str):
    """Materialize a service (once); None when never registered."""
    with _lock:
        if name in _cache:
            return _cache[name]
        producer = _producers.get(name)
        if producer is None:
            return None
        value = producer()
        _cache[name] = value
        return value


def has(name: str) -> bool:
    with _lock:
        return name in _producers


def producer(name: str):
    with _lock:
        return _producers.get(name)


def reset() -> None:
    """Tests only: drop all registrations and cached values."""
    with _lock:
        _producers.clear()
        _cache.clear()
