"""Addons module — zero-boilerplate tool drops.

suijin/addons/<name>/main.py: every public callable becomes an agent
tool (docstring -> description, signature -> parameters). The manifest
is synthesized in memory at scan time — nothing is written to disk.
`suijin module adopt <name>` graduates an addon into a full pack.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

from suijin.kernel.contracts import Module, Tier


def addon_roots() -> list[Path]:
    """Bundled package addons dir (wheel-shipped)."""
    return [Path(__file__).resolve().parents[2] / "addons"]


def _doc_of(fn) -> str:
    doc = inspect.getdoc(fn) or ""
    return doc.splitlines()[0].strip() if doc else f"{fn.__name__} (addon tool)"


def _params_of(fn) -> list[str]:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return []
    return [
        p.name
        for p in sig.parameters.values()
        if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY) and p.name not in ("self", "cls")
    ]


def scan_addons(roots: list[Path] | None = None) -> dict[str, dict]:
    """{addon_name: {tool_name: {fn, description, params}}} for every
    non-underscore addon dir containing main.py."""
    roots = roots if roots is not None else addon_roots()
    out: dict[str, dict] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or folder.name.startswith(("_", ".")):
                continue
            main = folder / "main.py"
            if not main.is_file():
                continue
            mod_name = f"suijin_addon.{folder.name}"
            try:
                if mod_name not in sys.modules:
                    spec = importlib.util.spec_from_file_location(mod_name, main)
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[mod_name] = mod
                    spec.loader.exec_module(mod)
                mod = sys.modules[mod_name]
            except Exception:  # noqa: BLE001 — a broken addon never breaks boot
                continue
            tools = {}
            for attr in dir(mod):
                if attr.startswith("_"):
                    continue
                fn = getattr(mod, attr, None)
                if not callable(fn) or getattr(fn, "__module__", None) != mod_name:
                    continue  # public, defined HERE (not an import)
                tools[attr] = {"fn": fn, "description": _doc_of(fn), "params": _params_of(fn)}
            if tools:
                out[folder.name] = tools
    return out


def get_addon_tools() -> dict[str, callable]:
    """Flat {tool_name: callable} across all addons."""
    flat: dict[str, callable] = {}
    for _name, tools in scan_addons().items():
        for tname, meta in tools.items():
            flat[tname] = meta["fn"]
    return flat


def catalog_text() -> str:
    """Prompt catalog section for addon tools (mirrors the pack style)."""
    addons = scan_addons()
    if not addons:
        return ""
    lines = ["## Addon Tools"]
    for name, tools in sorted(addons.items()):
        lines.append(f"### {name}")
        for tname, meta in sorted(tools.items()):
            args = ", ".join(f'"{p}": "..."' for p in meta["params"])
            lines.append(f"- **{tname}** — {meta['description']}")
            lines.append("  ```json")
            lines.append(
                f'  {{"tool": "{tname}", "args": {{{args}}}}}' if args else f'  {{"tool": "{tname}", "args": {{}}}}'
            )
            lines.append("  ```")
    return "\n".join(lines) + "\n"


class PackModule(Module):
    id = "addons"
    tier = Tier.RECOMMENDED

    def register(self, ctx) -> None:
        ctx.register_service("addons.catalog", catalog_text)

    def start(self, ctx) -> None:
        bridged = 0
        for tname, fn in get_addon_tools().items():
            if ctx.has_tool(tname):
                continue

            def _bridge(args, _ctx, _fn=fn):
                try:
                    return str(_fn(**(args or {})))
                except TypeError:
                    return str(_fn(*(args or {}).values()))

            ctx.register_tool(tname, _bridge, description=f"addon tool {tname}", owner="addons")
            bridged += 1
        if bridged:
            ctx.journal.append("addons", f"{bridged} addon tool(s) bridged")

    def stop(self, ctx) -> None:
        pass
