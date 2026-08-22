"""Auto-generated pack entry — do not edit by hand.

SELF-CONTAINED (Phase 5): loads this pack's own manifest.json + main.py
directly from the pack directory. No shared bridge, no imports outside
the pack — each plugin is a standalone lego brick.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from suijin.kernel.contracts import Module, Tier

_PACK_DIR = Path(__file__).resolve().parent


def _load_tools() -> dict:
    """Declared tools from this pack's own main.py, loaded by file path."""
    manifest = json.loads((_PACK_DIR / "manifest.json").read_text())
    declared = sorted((manifest.get("tools") or {}).keys())
    canonical = f"suijin_pack.{_PACK_DIR.name.lower()}"
    if canonical not in sys.modules:
        spec = importlib.util.spec_from_file_location(canonical, _PACK_DIR / "main.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[canonical] = mod
        spec.loader.exec_module(mod)
    mod = sys.modules[canonical]
    out = {}
    for n in declared:
        fn = getattr(mod, n, None)
        if callable(fn):
            params = list((manifest.get("tools") or {}).get(n, {}).get("parameters", {}) or [])
            out[n] = (fn, params)
    return out


class PackModule(Module):
    id = "awsenumer"
    tier = Tier.RECOMMENDED

    def register(self, ctx) -> None:
        pass

    def start(self, ctx) -> None:
        bridged = 0
        for tool_name, (fn, params) in _load_tools().items():
            if ctx.has_tool(tool_name):
                continue

            def _bridge(args, _ctx, _fn=fn):
                try:
                    return str(_fn(**(args or {})))
                except TypeError:
                    return str(_fn(*(args or {}).values()))

            ctx.register_tool(
                tool_name,
                _bridge,
                description="AWS enumeration: identity, S3 buckets (+public-read check), IAM roles/users, EC2 instances, Secrets Manager names, Lambda functions. Requires aws CLI with valid credentials.",
                owner="awsenumer",
                params=params,
            )
            bridged += 1
        ctx.journal.append("awsenumer", f"{bridged} tool(s) registered")

    def stop(self, ctx) -> None:
        pass
