"""Module management API — the single source of truth for installs.

One module BOTH the CLI verbs and the Textual Module Manager call:
install / uninstall / enable / disable / list / info. State is files:
  ~/.suijin/modules.json      enable/disable map
  ~/.suijin/modules/<id>/     installed (community) modules
Bundled modules (wheel) can be disabled but never uninstalled. Install
validates manifests, reports missing python deps with the exact pip
command (--with-deps opts in), and refuses core-tier imposters in user
space. Stdlib only.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from suijin.kernel.registry import Registry

STATE_DIR = Path.home() / ".suijin"
USER_MODULES = STATE_DIR / "modules"
_STATE_FILE = "modules.json"


class InstallError(Exception):
    """Install/uninstall refused — message is operator-facing."""


# ── enable/disable state ───────────────────────────────────────────────


def _state_path() -> Path:
    return STATE_DIR / _STATE_FILE


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_state(data: dict) -> None:
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(data, indent=2))


def is_enabled(module_id: str) -> bool:
    return _load_state().get(module_id, True)


def set_enabled(module_id: str, enabled: bool) -> bool:
    """Toggle a DISCOVERED module. Unknown ids are refused (typos must
    not silently write dead state)."""
    if module_id not in _all_units(None).units and not (USER_MODULES / module_id).exists():
        return False
    state = _load_state()
    if enabled:
        state.pop(module_id, None)
    else:
        state[module_id] = False
    _save_state(state)
    return True


# ── discovery / listing ────────────────────────────────────────────────


def _all_units(module_roots: list[Path] | None):
    reg = Registry()
    roots = list(module_roots or [])
    roots.append(Path(__file__).resolve().parent)  # bundled modules always known
    if USER_MODULES.is_dir():
        roots.append(USER_MODULES)
    for root in roots:
        reg.scan(Path(root))
    return reg.resolve()


def list_modules(module_roots: list[Path] | None = None) -> list[dict]:
    """Every discovered module with tier + enable state (boot order)."""
    report = _all_units(module_roots)
    out = []
    for unit in report.boot_order:
        out.append(
            {
                "id": unit.id,
                "version": unit.version,
                "tier": unit.tier.name.lower(),
                "requires": unit.requires,
                "description": getattr(unit, "config", {}).get("description", ""),
                "enabled": is_enabled(unit.id) and unit.id not in report.skipped,
                "permissions": unit.permissions,
            }
        )
    return out


def module_info(module_id: str, module_roots: list[Path] | None = None) -> dict:
    report = _all_units(module_roots)
    unit = report.units.get(module_id)
    if unit is None:
        raise InstallError(f"unknown module '{module_id}'")
    return {
        "id": unit.id,
        "version": unit.version,
        "tier": unit.tier.name.lower(),
        "requires": unit.requires,
        "provides": unit.provides,
        "permissions": unit.permissions,
        "enabled": is_enabled(unit.id),
        "source": str(unit.source) if unit.source else "bundled",
        "skipped": report.skipped.get(module_id, ""),
    }


# ── install / uninstall ────────────────────────────────────────────────


def install(source: str, with_deps: bool = False) -> str:
    """Install a module from a local path. Validates, reports, copies.

    Never executes anything from the source. Missing python deps are
    reported with the exact pip command; --with-deps installs them on
    explicit request only.
    """
    src = Path(source).expanduser().resolve()
    if not src.is_dir():
        raise InstallError(f"source directory not found: {src}")
    mf = src / "plugin.json"
    if not mf.exists():
        raise InstallError(f"no plugin.json in {src} — not a module")
    try:
        manifest = json.loads(mf.read_text())
    except ValueError as e:
        raise InstallError(f"unparseable manifest: {e}") from e
    mid = str(manifest.get("id", "")).strip()
    if not mid:
        raise InstallError("manifest has no 'id'")
    if str(manifest.get("tier", "")).lower() == "core":
        raise InstallError(
            f"'{mid}' declares tier=core — community modules may not be core "
            "(core modules ship with Suijin and cannot be user-installed)"
        )

    notes: list[str] = []

    # dependency report against the bundled tree
    requires = [str(r) for r in manifest.get("requires", [])]
    reg = Registry()
    reg.scan(Path(__file__).resolve().parent)  # bundled modules
    report = reg.resolve()
    missing = [d for d in requires if d not in report.bootable]
    if missing:
        notes.append(f"missing dependency(ies): {', '.join(missing)} (module will be skipped at boot until provided)")

    # python deps: report exact pip command; install only on explicit opt-in
    py_deps = [str(p) for p in manifest.get("python_deps", [])]
    if py_deps:
        cmd = f"pip install {' '.join(py_deps)}"
        if with_deps:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", *py_deps], capture_output=True, text=True, timeout=600
            )
            notes.append(f"python deps installed ({'ok' if r.returncode == 0 else 'FAILED — run: ' + cmd})")
        else:
            notes.append(f"python deps NOT installed — run: {cmd} (or reinstall with --with-deps)")

    dest = USER_MODULES / mid
    if dest.exists():
        shutil.rmtree(dest)  # reinstall = replace
    dest.mkdir(parents=True)
    for item in src.iterdir():
        if item.name in ("__pycache__", ".git"):
            continue
        shutil.copy2(item, dest / item.name) if item.is_file() else shutil.copytree(item, dest / item.name)

    result = f"installed '{mid}' v{manifest.get('version', '?')} -> {dest}"
    return result + ("\n  " + "\n  ".join(notes) if notes else "")


def uninstall(module_id: str) -> bool:
    """Remove an installed (user-space) module. Bundled modules refused."""
    target = USER_MODULES / module_id
    if not target.exists():
        if _bundled(module_id):
            raise InstallError(
                f"'{module_id}' is bundled with Suijin — disable it instead (suijin module disable) or remove the wheel"
            )
        raise InstallError(f"'{module_id}' is not installed in user space")
    shutil.rmtree(target)
    set_enabled(module_id, True)  # clear any stale disable state
    return True


def _bundled(module_id: str) -> bool:
    reg = Registry()
    reg.scan(Path(__file__).resolve().parent)
    return module_id in reg.resolve().units
