"""Pack converter — generates kernel plugin.json for every Modules/ pack.

Reads each pack's manifest.json (the Phase-2-era pack format: name,
tools, dependencies) and emits plugin.json (kernel format: id, tier,
requires, entry, permissions) plus a tiny entry shim that registers the
pack's tool callables on the Context at start().

Idempotent: re-running refreshes plugin.json files. Collisions between
packs (same id under Tools/ and Mods/) are recorded, not silently lost.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

# binaries that imply a shell permission; everything else is filesystem-only
_SHELL_HINTS = {
    "nmap",
    "gobuster",
    "sqlmap",
    "hydra",
    "ffuf",
    "feroxbuster",
    "nikto",
    "nuclei",
    "amass",
    "subfinder",
    "httpx",
    "whatweb",
    "sslscan",
    "john",
    "katana",
    "msfconsole",
    "curl",
    "dig",
    "aircrack-ng",
    "airodump-ng",
    "socat",
    "masscan",
    "trufflehog",
    "metasploit",
}


@dataclass
class ConversionResult:
    converted: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    collisions: list[str] = field(default_factory=list)


def _permissions_for(manifest: dict) -> list[str]:
    perms = {"filesystem", "network"}
    deps = manifest.get("dependencies") or []
    if any(str(d).split("/")[-1] in _SHELL_HINTS or str(d) in _SHELL_HINTS for d in deps):
        perms.add("shell")
    return sorted(perms)


_ENTRY_TEMPLATE = '''"""Auto-generated pack entry — do not edit by hand.

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
    id = "@@ID@@"
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

            ctx.register_tool(tool_name, _bridge, description=@@DESC@@,
                              owner="@@ID@@", params=params)
            bridged += 1
        ctx.journal.append("@@ID@@", f"{bridged} tool(s) registered")

    def stop(self, ctx) -> None:
        pass
'''


def convert_tree(source: Path, dest: Path) -> ConversionResult:
    """Convert every pack under source/{Tools,Mods} into dest (mirrored)."""
    result = ConversionResult()
    source = Path(source)
    dest = Path(dest)
    seen: set[str] = set()

    for category in ("Tools", "Mods"):
        cat_dir = source / category
        if not cat_dir.is_dir():
            continue
        for manifest_path in sorted(cat_dir.glob("*/manifest.json")):
            pack_dir = manifest_path.parent
            try:
                manifest = json.loads(manifest_path.read_text())
            except ValueError as e:
                result.skipped[pack_dir.name] = f"unparseable manifest: {e}"
                continue
            # id from the DIRECTORY name (stable, matches module paths);
            # the manifest "name" is a display label only
            pid = pack_dir.name.strip().lower().replace(" ", "_")
            if not pid or not pid.replace("_", "").isalnum():
                result.skipped[pack_dir.name] = "unusable directory id"
                continue
            if pid in seen:
                result.collisions.append(pid)
                continue
            seen.add(pid)

            # FLAT layout: dest/<id>/ — the kernel has no Tools/Mods
            # concept; category is preserved as manifest metadata only
            out_dir = dest / pack_dir.name
            out_dir.mkdir(parents=True, exist_ok=True)

            tools = manifest.get("tools") or {}
            tool_names = sorted(tools.keys())
            desc = str(manifest.get("description", f"{pid} tool pack"))[:200]

            plugin = {
                "id": pid,
                "version": str(manifest.get("version", "1.0")),
                "tier": "recommended",
                "requires": ["platform", "tools"],
                "provides": [f"pack.{pid}"] + tool_names[:10],
                "entry": f"pack_entry:{pack_dir.name}",  # resolved from entry.py beside the manifest
                "category": category,
                "permissions": _permissions_for(manifest),
                "description": desc,
            }
            # copy the pack's real source (manifest.json + main.py + extras)
            # so the generated self-contained entry can load them
            for item in pack_dir.iterdir():
                if item.name in ("plugin.json", "entry.py", "__pycache__"):
                    continue
                target = out_dir / item.name
                if target.exists():
                    continue
                shutil.copy2(item, target) if item.is_file() else shutil.copytree(item, target)
            (out_dir / "plugin.json").write_text(json.dumps(plugin, indent=2))
            (out_dir / "entry.py").write_text(_ENTRY_TEMPLATE.replace("@@ID@@", pid).replace("@@DESC@@", repr(desc)))
            # wheel-shippable: pack dirs are packages (setuptools find) and
            # their non-.py assets ride via package-data
            init = out_dir / "__init__.py"
            if not init.exists():
                init.write_text('"""Vendored third-party tool pack (converted).\n"""\n')
            result.converted.append(pid)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Convert Modules/ packs to kernel plugins")
    parser.add_argument("--source", required=True, help="the Modules/ root")
    parser.add_argument("--dest", required=True, help="output root for converted packs")
    args = parser.parse_args(argv)

    result = convert_tree(Path(args.source), Path(args.dest))
    print(f"converted: {len(result.converted)} pack(s)")
    for pid in result.collisions:
        print(f"  COLLISION: {pid} (kept first occurrence)")
    for name, why in result.skipped.items():
        print(f"  skipped: {name} ({why})")
    return 0 if not result.collisions else 1


if __name__ == "__main__":
    sys.exit(main())
