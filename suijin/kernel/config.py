"""Kernel layered config — kernel defaults → module defaults → user → env.

Defined ONCE here so configuration stops depending on import order.
Layers are immutable after sealing; later layers shadow earlier ones.
Stdlib only.
"""

from __future__ import annotations

from typing import Any


class LayeredConfig:
    """Ordered shadowing configuration with immutable snapshots."""

    def __init__(self) -> None:
        self._layers: list[tuple[str, dict]] = []

    def add_layer(self, name: str, data: dict) -> None:
        """Append a layer (later layers shadow earlier). Dicts are copied."""
        self._layers.append((name, dict(data)))

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

    def snapshot(self) -> dict:
        """Merged view (new dict — mutating it never touches the layers)."""
        merged: dict = {}
        for _name, data in self._layers:
            merged.update(data)
        return merged

    def layer_names(self) -> list[str]:
        return [name for name, _ in self._layers]
