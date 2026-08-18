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

Registers the pack's tool callables (declared in manifest.json) on the
Context at start(). Regenerate with suijin/modules/pack_converter.py.
"""

from __future__ import annotations

from suijin.kernel.contracts import Module, Tier


class PackModule(Module):
    id = "{id}"
    tier = Tier.RECOMMENDED

    def register(self, ctx) -> None:
        pass  # tools bridge at start (needs platform's runtime init)

    def start(self, ctx) -> None:
        from suijin.modules._packbridge import load_pack_tools

        fns = load_pack_tools("{id}")
        bridged = 0
        for tool_name in {tool_names!r}:
            fn = fns.get(tool_name)
            if fn is None or ctx.has_tool(tool_name):
                continue

            def _bridge(args, _ctx, _fn=fn):
                return str(_fn(**(args or {{}})))

            ctx.register_tool(tool_name, _bridge, description={desc!r},
                              owner="{id}")
            bridged += 1
        ctx.journal.append("{id}", f"{{bridged}} tool(s) registered")

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
            (out_dir / "plugin.json").write_text(json.dumps(plugin, indent=2))
            (out_dir / "entry.py").write_text(_ENTRY_TEMPLATE.format(id=pid, tool_names=tool_names, desc=desc))
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
