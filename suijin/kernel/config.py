"""Kernel layered config — kernel defaults → module defaults → user → env.

Defined ONCE here so configuration stops depending on import order.
Layers are immutable after sealing; later layers shadow earlier ones.
Stdlib only.
"""

from __future__ import annotations

import copy
from typing import Any


class LayeredConfig:
    """Ordered shadowing configuration with immutable snapshots."""

    def __init__(self) -> None:
        self._layers: list[tuple[str, dict]] = []

    def add_layer(self, name: str, data: dict) -> None:
        """Append a layer (later layers shadow earlier). Dicts are copied
        DEEPLY — mutating the caller's dict after this call never leaks
        into the sealed layer (hardening: the old shallow dict() shared
        nested sections with the caller)."""
        self._layers.append((name, copy.deepcopy(data)))

    def __getitem__(self, key: str) -> Any:
        for _name, data in reversed(self._layers):
            if key in data:
                return data[key]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        for _name, data in reversed(self._layers):
            if key in data:
                return data[key]
        return default

    def __contains__(self, key: str) -> bool:
        return any(key in data for _name, data in self._layers)

    @staticmethod
    def _deep_merge(base: dict, overlay: dict) -> dict:
        """Overlay onto base; nested dicts merge recursively so a layer
        that overrides ONE key inside a section keeps the others (a
        shallow update would wipe the whole section — kernel killer)."""
        out = dict(base)
        for key, value in overlay.items():
            if key in out and isinstance(out[key], dict) and isinstance(value, dict):
                out[key] = LayeredConfig._deep_merge(out[key], value)
            else:
                out[key] = value
        return out

    def snapshot(self) -> dict:
        """Merged view (new dict — mutating it never touches the layers).

        Nested dicts DEEP-merge: a layer overriding one key inside a
        section preserves that section's other keys."""
        merged: dict = {}
        for _name, data in self._layers:
            merged = LayeredConfig._deep_merge(merged, data)
        # deep copy: mutating the returned snapshot (or its nested lists)
        # never reaches the sealed layers
        return copy.deepcopy(merged)

    def layer_names(self) -> list[str]:
        return [name for name, _ in self._layers]
