"""
suijin/modules/loader.py
========================
Scans the repo-root Modules/ directory (Modules/Tools/, Modules/Mods/) for
modular tool packs.

Each module is a folder containing:
  manifest.json — metadata (name, version, tools, dependencies)
  main.py       — tool implementations (auto-imported)
  skill.md      — AI usage instructions (auto-injected into system prompt)

The loader discovers all modules via discover_modules() and makes their tools
available via get_module_tools() and their skills via get_module_skills().
"""

import importlib.util
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # suijin/ root
# v4.1: packs are vendored INTO the module home (suijin/modules/<pack>/)
# plus the user extension dir (~/.suijin/modules). The legacy repo-level
# Modules/Tools tree is gone.
PACK_ROOTS = [BASE_DIR / "modules", Path.home() / ".suijin" / "modules"]

# Recon profiles, container images, DNS records — defined inline


_verbose = False


def set_verbose(v: bool):
    global _verbose
    _verbose = v


# ── Centralized force-load helper ─────────────────────────────────────
# Many suijin modules need to import sibling .py files bypassing the
# installed package. This single helper replaces the 6 duplicate copies
# of importlib.util.spec_from_file_location scattered across the codebase.

# Canonical name -> module object. One instance per file, forever: the old
# behavior re-executed the file on every call, spawning split-brain
# instances (providers.py ran FIVE times, each with its own USAGE
# accumulator — "shares ONE instance" comments were false).
_local_cache: dict[str, object] = {}

# Search order: root first, then subdirs. The canonical name for caching
# is the resolved file's real import path (e.g. "suijin.modules.providers.lib"),
# so force-loaded and normally-imported modules share sys.modules.
SEARCH_DIRS = [BASE_DIR]  # v4.1: all subsystems live in modules/ — by-name loads go through CANONICAL_ALIASES

# v4.1 clean break: names whose old files are gone map straight to their
# canonical module homes (dynamic load == normal import, always).
CANONICAL_ALIASES = {
    "providers": "suijin.modules.providers.lib",
    "audit": "suijin.modules.platform.lib.security.audit",
    "supervisor": "suijin.modules.redteam.lib.intel.supervisor",
    "oracle": "suijin.modules.redteam.lib.intel.oracle",
    "knowledge_graph": "suijin.modules.redteam.lib.intel.knowledge_graph",
    "drift_analyser": "suijin.modules.redteam.lib.intel.drift_analyser",
}


def load_local_module(mod_name: str):
    """Import a sibling .py by name, searching suijin/ root and subdirs.

    Contract (Phase 0, item 3):
      - returns the SAME module object on every call for a given file
      - when the file maps to a real package module, the sys.modules entry
        IS that module (dynamic load == normal import)
      - raises ModuleNotFoundError (clear, catchable) when nothing matches
    """
    import importlib.util

    if mod_name in CANONICAL_ALIASES:
        return __import__(CANONICAL_ALIASES[mod_name], fromlist=["_"])

    for search in SEARCH_DIRS:
        path = search / f"{mod_name}.py"
        if not path.exists():
            continue
        # Canonical import path: suijin/<rel>.py -> suijin.<rel with dots>
        try:
            rel = path.resolve().relative_to(BASE_DIR.resolve()).with_suffix("")
            canonical = "suijin." + ".".join(rel.parts)
        except ValueError:
            canonical = f"suijin_local.{mod_name}"
        cached = sys.modules.get(canonical)
        if cached is not None:
            return cached
        spec = importlib.util.spec_from_file_location(canonical, str(path))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[canonical] = mod  # register BEFORE exec (self-import safety)
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(canonical, None)
            raise
        return mod
    raise ModuleNotFoundError(
        f"suijin module '{mod_name}' not found in any search dir ({', '.join(str(d.name) for d in SEARCH_DIRS)})"
    )


def discover_modules():
    """Scan the pack roots (vendored suijin/modules + ~/.suijin/modules) and load tool packs.

    A pack = a directory with manifest.json (+ main.py / skill.md).
    Called once at startup. Safe to call multiple times (idempotent).
    """
    global _loaded_modules, _module_tools, _module_skills

    _loaded_modules = {}
    _module_tools = {}
    _module_skills = []

    total_modules = 0
    total_tools = 0
    total_skills = 0

    pack_dirs: list[Path] = []
    seen: set[str] = set()
    for root in PACK_ROOTS:
        if not root.is_dir():
            continue
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or folder.name.startswith((".", "__pycache__")):
                continue
            if folder.name in seen:
                continue  # vendored copy wins over a user dir of the same name
            if (folder / "manifest.json").exists():
                pack_dirs.append(folder)
                seen.add(folder.name)

    for folder in pack_dirs:
        manifest_path = folder / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        key = folder.name
        _loaded_modules[key] = {"manifest": manifest, "tools": {}, "skill": ""}

        py_file = folder / "main.py"
        tools_found = 0
        if py_file.exists():
            try:
                mod = _force_load_module(f"suijin_pack.{folder.name}", str(py_file))
                for tool_name in manifest.get("tools") or {}:
                    func = getattr(mod, tool_name, None)
                    if callable(func):
                        _module_tools[tool_name] = func
                        _loaded_modules[key]["tools"][tool_name] = func
                        tools_found += 1
            except Exception as e:
                if _verbose:
                    print(f"[ModuleLoader] FAILED {key}: {e}")
                continue

        skill_path = folder / "skill.md"
        if skill_path.exists():
            try:
                skill_text = skill_path.read_text(encoding="utf-8", errors="ignore")
                _loaded_modules[key]["skill"] = skill_text
                _module_skills.append((manifest.get("name", key), skill_text))
            except Exception:
                pass

        if tools_found > 0 or _loaded_modules[key]["skill"]:
            total_modules += 1
            total_tools += tools_found
            if _loaded_modules[key]["skill"]:
                total_skills += 1

    if _verbose and total_modules > 0:
        print(f"[ModuleLoader] {total_modules} modules ({total_tools} tools, {total_skills} skills)")


def _force_load_module(module_name, file_path):
    """Import a Python file as a module by path."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def get_module_tools():
    """Return dict of {tool_name: callable} from all loaded modules.

    Call discover_modules() first. Tolerates the undiscovered state
    (returns {} instead of NameError — route_tool must work pre-boot).
    """
    return dict(globals().get("_module_tools") or {})


def get_module_skills():
    """Return merged skill documentation from all loaded modules.

    Returns a string suitable for injection into the system prompt.
    Call discover_modules() first. Tolerates the undiscovered state
    (returns {} instead of NameError — route_tool must work pre-boot).
    """
    if not _module_skills:
        return ""
    parts = []
    for mod_name, skill_text in _module_skills:
        parts.append(f"## Module: {mod_name}\n{skill_text}\n")
    return "\n".join(parts)


def get_loaded_modules():
    """Return a summary of all loaded modules."""
    return dict(_loaded_modules)
