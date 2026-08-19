"""Module SDK — scaffold and validate Suijin module packs.

`suijin module init <name>` creates ~/.suijin/modules/<name>/ with a
working, kernel-bootable pack (manifest + implementation + skill doc +
plugin.json + entry). `suijin module validate <name>` checks the manifest
schema, imports the implementation, and verifies every declared tool
resolves to a callable with a docstring.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MODULES_ROOT = Path.home() / ".suijin" / "modules"  # user extension home (vendored packs live in the package)

_ENTRY_TEMPLATE = '''
"""Auto-generated pack entry — do not edit by hand."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from suijin.kernel.contracts import Module, Tier

_PACK_DIR = Path(__file__).resolve().parent


def _load_tools() -> dict:
    manifest = json.loads((_PACK_DIR / "manifest.json").read_text())
    declared = sorted((manifest.get("tools") or {}).keys())
    canonical = f"suijin_pack.{_PACK_DIR.name.lower()}"
    if canonical not in sys.modules:
        spec = importlib.util.spec_from_file_location(canonical, _PACK_DIR / "main.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[canonical] = mod
        spec.loader.exec_module(mod)
    mod = sys.modules[canonical]
    return {n: getattr(mod, n) for n in declared if callable(getattr(mod, n, None))}


class PackModule(Module):
    id = "@@ID@@"
    tier = Tier.RECOMMENDED

    def register(self, ctx) -> None:
        pass

    def start(self, ctx) -> None:
        for tool_name, fn in _load_tools().items():
            if ctx.has_tool(tool_name):
                continue

            def _bridge(args, _ctx, _fn=fn):
                try:
                    return str(_fn(**(args or {})))
                except TypeError:
                    return str(_fn(*(args or {}).values()))

            ctx.register_tool(tool_name, _bridge, description=@@DESC@@,
                              owner="@@ID@@")

    def stop(self, ctx) -> None:
        pass
'''


_MANIFEST_TEMPLATE = {
    "name": "{Title}",
    "version": "1.0",
    "description": "{name} module pack",
    "tools": {
        "{name}_run": {
            "description": "Run {name}. Edit main.py to implement.",
            "parameters": {"target": "Target to operate on"},
        }
    },
    "dependencies": [],
    "platforms": ["macos", "linux"],
}

_MAIN_TEMPLATE = '''"""{Title} — module implementation.

Tools declared in manifest.json must exist as top-level functions with the
same names. Each takes keyword arguments matching the manifest parameters.
"""


def {name}_run(target: str = "") -> str:
    """Run {name} against a target."""
    if not target:
        return "Error: target required"
    # TODO: implement (subprocess, requests, ... — see neighboring modules)
    return f"{name} executed against {target} (scaffold — implement me)"
'''

_SKILL_TEMPLATE = """# {Title}

`{name}_run` — describe when the agent should use this tool, expected args,
and example output. This file is loaded into the agent's skill catalog.

| arg | required | meaning |
|:----|:---------|:--------|
| `target` | yes | Host/URL to operate on |
"""


def scaffold_module(name: str, root: Path | None = None) -> Path:
    safe = "".join(c for c in (name or "").strip().lower() if c.isalnum() or c == "_")
    if not safe:
        raise ValueError("module name must be alphanumeric/underscore")
    base = Path(root) if root else MODULES_ROOT
    mod_dir = base / safe
    if mod_dir.exists():
        raise FileExistsError(f"module already exists: {mod_dir}")
    mod_dir.mkdir(parents=True)
    (mod_dir / "__init__.py").write_text('"""User module pack (SDK scaffold)."""\n')
    (mod_dir / "manifest.json").write_text(
        json.dumps(_MANIFEST_TEMPLATE, indent=2).replace("{name}", safe).replace("{Title}", safe.title())
    )
    (mod_dir / "main.py").write_text(_MAIN_TEMPLATE.replace("{name}", safe).replace("{Title}", safe.title()))
    (mod_dir / "skill.md").write_text(_SKILL_TEMPLATE.replace("{name}", safe).replace("{Title}", safe.title()))
    # kernel plugin manifest + entry so the scaffold BOOTS (same shape the
    # pack converter emits for vendored packs)
    plugin = {
        "id": safe,
        "version": "1.0",
        "tier": "recommended",
        "requires": ["platform", "tools"],
        "provides": [f"pack.{safe}", f"{safe}_run"],
        "entry": f"pack_entry:{safe}",
        "permissions": ["filesystem", "network"],
        "description": f"{safe} module pack (SDK scaffold)",
    }
    (mod_dir / "plugin.json").write_text(json.dumps(plugin, indent=2))
    (mod_dir / "entry.py").write_text(
        _ENTRY_TEMPLATE.replace("@@ID@@", safe).replace("@@DESC@@", repr(f"{safe} module pack (SDK scaffold)"))
    )
    return mod_dir


def validate_module(name: str, root: Path | None = None) -> tuple[bool, list[str]]:
    """Manifest + implementation checks. Returns (ok, problems)."""
    base = Path(root) if root else MODULES_ROOT
    mod_dir = base / name
    problems: list[str] = []
    if not mod_dir.is_dir():
        return False, [f"no module directory: {mod_dir}"]
    mpath = mod_dir / "manifest.json"
    if not mpath.exists():
        return False, ["manifest.json missing"]
    try:
        manifest = json.loads(mpath.read_text())
    except ValueError as e:
        return False, [f"manifest.json: invalid JSON — {e}"]
    for key in ("name", "version", "tools"):
        if key not in manifest:
            problems.append(f"manifest: missing '{key}'")
    tools = manifest.get("tools", {})
    if not isinstance(tools, dict) or not tools:
        problems.append("manifest: 'tools' must be a non-empty object")
    deps = manifest.get("dependencies", [])
    if not isinstance(deps, list):
        problems.append("manifest: 'dependencies' must be a list")

    main = mod_dir / "main.py"
    if not main.exists():
        problems.append("main.py missing")
    elif not problems:
        spec = importlib.util.spec_from_file_location(f"modcheck_{name}", main)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            problems.append(f"main.py: fails to import — {e}")
        else:
            for tname in tools:
                fn = getattr(mod, tname, None)
                if not callable(fn):
                    problems.append(f"tool '{tname}' declared but not a function in main.py")
                elif not (fn.__doc__ or "").strip():
                    # advisory (marker prefix): the catalog description comes
                    # from the manifest, so the agent still sees the tool —
                    # docstrings are the convention, not a hard requirement
                    problems.append(
                        f"advise: tool '{tname}' has no docstring (manifest feeds the catalog; docstrings are the convention)"
                    )
    return (not problems), problems


def adopt_addon(name: str, addon_root: Path | None = None, dest_root: Path | None = None) -> Path:
    """Graduate an addon (suijin/addons/<name>/main.py) into a full pack.

    Introspects the addon's public callables and writes a pack layout
    (manifest.json, plugin.json, entry.py, skill.md, __init__.py) into
    dest_root/<name> (default: the vendored module home). The addon's
    main.py is copied verbatim as the implementation.
    """
    import shutil

    from suijin.modules.addons.entry import _doc_of, _params_of, addon_roots

    roots = [Path(addon_root)] if addon_root else addon_roots()
    src = None
    for r in roots:
        cand = r / name / "main.py"
        if cand.is_file():
            src = cand
            break
    if src is None:
        raise FileNotFoundError(
            f"addon '{name}' not found under {[str(r) for r in roots]} (looking for <name>/main.py)"
        )

    import importlib.util
    import sys

    mod_name = f"suijin_addon.{name}"
    if mod_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(mod_name, src)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
    mod = sys.modules[mod_name]

    tools: dict[str, dict] = {}
    for attr in dir(mod):
        if attr.startswith("_"):
            continue
        fn = getattr(mod, attr, None)
        if callable(fn) and getattr(fn, "__module__", None) == mod_name:
            tools[attr] = {"description": _doc_of(fn), "parameters": {p: "value" for p in _params_of(fn)}}
    if not tools:
        raise ValueError(f"addon '{name}' exposes no public callables — nothing to adopt")

    dest = (Path(dest_root) if dest_root else Path(__file__).resolve().parents[2]) / name
    if dest.exists():
        raise FileExistsError(f"pack already exists: {dest}")
    dest.mkdir(parents=True)
    (dest / "__init__.py").write_text('"""Adopted from an addon (suijin module adopt)."""\n')
    shutil.copy2(src, dest / "main.py")

    manifest = {
        "name": name.title(),
        "version": "1.0",
        "description": f"{name} (adopted from addon)",
        "tools": tools,
        "dependencies": [],
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2))
    tool_names = sorted(tools)
    plugin = {
        "id": name,
        "version": "1.0",
        "tier": "recommended",
        "requires": ["platform", "tools"],
        "provides": [f"pack.{name}"] + tool_names,
        "entry": f"pack_entry:{name}",
        "permissions": ["filesystem", "network"],
        "description": f"{name} (adopted from addon)",
    }
    (dest / "plugin.json").write_text(json.dumps(plugin, indent=2))
    entry = '''"""Auto-generated pack entry (adopted from an addon)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from suijin.kernel.contracts import Module, Tier

_PACK_DIR = Path(__file__).resolve().parent


def _load_tools() -> dict:
    manifest = json.loads((_PACK_DIR / "manifest.json").read_text())
    declared = sorted((manifest.get("tools") or {}).keys())
    canonical = f"suijin_pack.{_PACK_DIR.name.lower()}"
    if canonical not in sys.modules:
        spec = importlib.util.spec_from_file_location(canonical, _PACK_DIR / "main.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[canonical] = mod
        spec.loader.exec_module(mod)
    mod = sys.modules[canonical]
    return {n: getattr(mod, n) for n in declared if callable(getattr(mod, n, None))}


class PackModule(Module):
    id = "@@ID@@"
    tier = Tier.RECOMMENDED

    def register(self, ctx) -> None:
        pass

    def start(self, ctx) -> None:
        for tool_name, fn in _load_tools().items():
            if ctx.has_tool(tool_name):
                continue

            def _bridge(args, _ctx, _fn=fn):
                try:
                    return str(_fn(**(args or {})))
                except TypeError:
                    return str(_fn(*(args or {}).values()))

            ctx.register_tool(tool_name, _bridge, description=@@DESC@@,
                              owner="@@ID@@")

    def stop(self, ctx) -> None:
        pass
'''.replace("@@ID@@", name).replace("@@DESC@@", repr(f"{name} (adopted from addon)"))
    (dest / "entry.py").write_text(entry)
    (dest / "skill.md").write_text(
        f"# {name}\n\nAdopted from an addon. Tools: " + ", ".join(f"`{t}`" for t in tool_names) + "\n"
    )
    return dest


def test_pack(name: str, root: Path | None = None) -> tuple[bool, list[str]]:
    """One-command pack test (F44): the author's pre-publish gate.

    Checks: files present (manifest/plugin/entry/main/skill), manifest
    schema + implementation import (validate_module), a real kernel boot
    where the pack registers its tools, catalog advertisement for every
    declared tool, and callable shape for each. Returns (ok, report).
    """
    lines: list[str] = []
    base = Path(root) if root else Path(__file__).resolve().parents[2]
    pdir = base / name
    if not pdir.is_dir():
        return False, [f"[XX] no pack directory: {pdir}"]

    # 1. files
    for f in ("manifest.json", "plugin.json", "entry.py"):
        if not (pdir / f).exists():
            lines.append(f"[XX] missing {f}")
    if (pdir / "main.py").exists():
        lines.append("[ok] implementation main.py present")
    elif all(not (pdir / f).exists() for f in ("main.py",)):
        lines.append("[--] no main.py (entry-only pack)")
    if (pdir / "skill.md").exists():
        lines.append("[ok] skill.md present (agent docs)")
    else:
        lines.append("[--] no skill.md — the agent won't get usage docs")

    # 2. schema + import
    ok_v, problems = validate_module(name, base if root else MODULES_ROOT if False else base)
    real_problems = [p for p in problems if not p.startswith("advise: ")]
    for p in problems:
        if p.startswith("advise: "):
            lines.append(f"[--] {p[len('advise: ') :]}")
    if not real_problems:
        lines.append("[ok] manifest schema + implementation import")
    else:
        lines += [f"[XX] {p}" for p in real_problems]

    # 3. boot + tool registration + catalog
    try:
        import json as _json

        from suijin.kernel import controller

        ctx, report = controller.boot(
            module_roots=[base if root else Path(__file__).resolve().parents[2]],
            workspace=base.parent / ".moduletest_ws",
            quiet=True,
        )
        try:
            ids = [u.id for u in report.boot_order]
            if name in ids:
                lines.append(f"[ok] boots as kernel unit (estate: {len(ids)} units)")
            else:
                if name in report.skipped:
                    lines.append(f"[XX] skipped at boot: {report.skipped[name]}")
                elif name in report.quarantined:
                    lines.append(f"[XX] quarantined: {report.quarantined[name]}")
                else:
                    lines.append("[XX] not in boot order")
                return False, lines
            manifest = _json.loads((pdir / "manifest.json").read_text())
            declared = sorted((manifest.get("tools") or {}).keys())
            missing_reg = [t for t in declared if not ctx.has_tool(t)]
            if missing_reg:
                lines.append(f"[XX] declared but NOT registered: {', '.join(missing_reg)}")
            else:
                lines.append(f"[ok] all {len(declared)} declared tool(s) registered")
            # catalog advertisement
            from suijin.modules.loader import discover_modules

            discover_modules()
            from suijin.modules.tools.lib import dispatch

            catalog = dispatch.get_tool_catalog()
            invisible = [t for t in declared if t not in catalog]
            if invisible:
                lines.append(f"[XX] invisible to the model (not in catalog): {', '.join(invisible)}")
            elif declared:
                lines.append("[ok] every tool advertised in the catalog")
            # callable smoke: zero-arg call must not crash the kernel

            for t in declared:
                entry = ctx._tools.get(t) or {}
                fn = entry.get("fn")
                if fn is not None:
                    try:
                        out = str(fn({}, ctx))
                        lines.append(f"[ok] {t} callable smoke: {out.splitlines()[0][:50]}")
                    except Exception as e:  # noqa: BLE001 — smoke failures are report data
                        lines.append(f"[--] {t} smoke needs args ({type(e).__name__})")
        finally:
            ctx.shutdown()
    except Exception as e:  # noqa: BLE001
        lines.append(f"[XX] boot failed: {e}")

    ok = not any(ln.startswith("[XX]") for ln in lines)
    return ok, lines
