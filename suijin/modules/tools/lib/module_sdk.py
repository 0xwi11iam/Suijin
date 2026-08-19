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
                    problems.append(f"tool '{tname}' missing a docstring (agents need it)")
    return (not problems), problems
