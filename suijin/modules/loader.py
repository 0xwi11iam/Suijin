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
MODULES_DIR = BASE_DIR.parent / "Modules"

# Recon profiles, container images, DNS records — defined inline
RECON_PROFILES = {
    "balanced": {
        "nmap_flags": "-sV -sC -T4",
        "gobuster_threads": 40,
        "ffuf_rate": 50,
        "delay_between_requests_ms": 200,
        "max_parallel_scans": 4,
    },
    "stealth": {
        "nmap_flags": "-sS -T2 --max-retries 1",
        "gobuster_threads": 10,
        "ffuf_rate": 5,
        "delay_between_requests_ms": 2000,
        "max_parallel_scans": 1,
    },
    "aggressive": {
        "nmap_flags": "-sV -sC -T5 --min-rate 1000",
        "gobuster_threads": 80,
        "ffuf_rate": 200,
        "delay_between_requests_ms": 50,
        "max_parallel_scans": 8,
    },
}


def get_profile(profile_name: str = "balanced") -> dict:
    return RECON_PROFILES.get(profile_name, RECON_PROFILES["balanced"])


TOOL_IMAGES = {}


def get_tool_image(tool_name: str) -> str:
    return TOOL_IMAGES.get(tool_name)


DNS_RECON_RECORDS = ["A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "PTR", "SRV"]
SUBDOMAIN_TIERS = {"tier1_always": ["www", "mail", "ftp", "admin", "api", "dev", "staging"]}

# Loaded module registry
_loaded_modules = {}
_module_tools = {}
_module_skills = []
_verbose = False  # Silent by default — redteamer.main() enables on startup


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
SEARCH_DIRS = [BASE_DIR] + [BASE_DIR / d for d in ("intel", "core")]

# v4.1 clean break: names whose old files are gone map straight to their
# canonical module homes (dynamic load == normal import, always).
CANONICAL_ALIASES = {
    "providers": "suijin.modules.providers.lib",
    "audit": "suijin.modules.platform.lib.security.audit",
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
    """Scan modules/ for valid module folders and load them.

    Called once at startup. Safe to call multiple times (idempotent).
    """
    global _loaded_modules, _module_tools, _module_skills

    if not MODULES_DIR.exists():
        return

    _loaded_modules = {}
    _module_tools = {}
    _module_skills = []

    # Scan one level deeper: modules/Tools/ and modules/Mods/
    total_modules = 0
    total_tools = 0
    total_skills = 0

    for category in sorted(MODULES_DIR.iterdir()):
        if not category.is_dir() or category.name.startswith("."):
            continue

        for folder in sorted(category.iterdir()):
            if not folder.is_dir() or folder.name.startswith(".") or folder.name == "__pycache__":
                continue

            manifest_path = folder / "manifest.json"
            if not manifest_path.exists():
                continue

            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            key = f"{category.name}/{folder.name}"
            _loaded_modules[key] = {"manifest": manifest, "tools": {}, "skill": ""}

            # Load the Python entry point (main.py)
            py_file = folder / "main.py"
            tools_found = 0
            if py_file.exists():
                try:
                    mod = _force_load_module(f"suijin_modules.{category.name}.{folder.name}", str(py_file))
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

            # Load skill documentation
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

    Call discover_modules() first.
    """
    return dict(_module_tools)


def get_module_skills():
    """Return merged skill documentation from all loaded modules.

    Returns a string suitable for injection into the system prompt.
    Call discover_modules() first.
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
