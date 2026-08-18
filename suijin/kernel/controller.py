"""Kernel controller — the init system. boot() is the composition root.

Scans module roots, resolves the DAG via the registry, materializes
module objects (entries injected by the caller during migration — later
phases import from the manifest's entry path), runs register() for all,
start() in dependency order, and honors the quiet-boot contract: silent
when healthy, human-readable report whenever anything was skipped,
quarantined, or collided. Stdlib only.
"""

from __future__ import annotations

import sys
from pathlib import Path

from suijin.kernel.context import Context
from suijin.kernel.health import HealthTracker
from suijin.kernel.jobs import JobScheduler
from suijin.kernel.journal import Journal
from suijin.kernel.registry import Registry
from suijin.kernel.vfs import Vfs

# The live boot's entry objects (tests + shutdown introspection).
_LAST_BOOT_ENTRIES: dict[str, object] = {}
_LAST_CONTEXT: Context | None = None


def _import_entry(entry: str) -> object | None:
    """Resolve 'pkg.module:Class' to an instance. None on any failure —
    the caller quarantines with the reason."""
    if not entry or ":" not in entry:
        return None
    mod_path, _, cls_name = entry.partition(":")
    try:
        __import__(mod_path)
        cls = getattr(sys.modules[mod_path], cls_name, None)
        return cls() if cls else None
    except Exception:
        return None


def boot(
    module_roots: list[Path] | None = None,
    entries: dict[str, object] | None = None,
    config: dict | None = None,
    workspace: str | Path | None = None,
    quiet: bool = True,
) -> tuple[Context, "object"]:
    """Compose the system. Returns (ctx, boot_report).

    entries: during migration, module objects are injected directly keyed
    by module id; ids without an entry fall back to importing the
    manifest's entry string. Phase 2+ makes manifests the only source.
    """
    global _LAST_BOOT_ENTRIES, _LAST_CONTEXT

    reg = Registry()
    for root in module_roots or []:
        reg.scan(Path(root))
    report = reg.resolve()
    if report.aborted:
        raise RuntimeError(f"boot aborted — {report.abort_reason}")

    # Materialize module objects
    entries = dict(entries or {})
    for unit in report.boot_order:
        uid = unit.id
        if uid in entries:
            continue
        obj = _import_entry(unit.entry)
        if obj is None:
            if unit.tier.value == 0:  # core without an object = boot problem
                raise RuntimeError(f"core module '{uid}' has no loadable entry")
            report.quarantined[uid] = f"entry not loadable: {unit.entry!r}"
    bootable_units = [u for u in report.boot_order if u.id not in report.quarantined]

    # register() for every module — failures quarantine (recommended) or abort (core)
    ctx = Context(config=config, workspace=workspace)
    ctx.vfs = Vfs(ctx.workspace)
    ctx.jobs = JobScheduler()
    ctx.journal = Journal(ctx.workspace / "logs")
    ctx.health = HealthTracker()
    ctx.journal.append("boot", report.summary())
    started: list[object] = []
    for unit in bootable_units:
        mod = entries.get(unit.id)
        if mod is None:
            continue
        try:
            mod.register(ctx)
        except Exception as e:  # noqa: BLE001
            if unit.tier.value == 0:
                raise RuntimeError(f"core module '{unit.id}' failed register: {e}") from e
            report.skipped[unit.id] = f"register failed: {e}"

    # start() in dependency order — failures skip the module, boot continues
    for unit in bootable_units:
        mod = entries.get(unit.id)
        if mod is None or unit.id in report.skipped:
            continue
        try:
            mod.start(ctx)
            started.append(mod)
            ctx.health.record_boot(unit.id, status="ok")
            ctx.journal.append("module.start", unit.id)
        except Exception as e:  # noqa: BLE001
            if unit.tier.value == 0:
                raise RuntimeError(f"core module '{unit.id}' failed start: {e}") from e
            report.skipped[unit.id] = f"start failed: {e}"
            ctx.health.record_boot(unit.id, status="failed", detail=str(e))
            ctx.journal.append("module.skip", f"{unit.id}: {e}")

    _LAST_BOOT_ENTRIES = {u.id: entries[u.id] for u in bootable_units if u.id in entries}
    _LAST_CONTEXT = ctx

    def _shutdown() -> list[str]:
        stopped = []
        for mod in reversed(started):
            try:
                mod.stop(ctx)
                stopped.append(getattr(mod, "id", "?"))
            except Exception:  # noqa: BLE001 — shutdown is best-effort
                pass
        # shutdown entry goes to disk (flush clears the ring by design)
        ctx.journal.append("boot", f"shutdown: {len(stopped)} module(s) stopped")
        ctx.journal.flush()
        return stopped

    ctx.shutdown = _shutdown  # type: ignore[method-assign]

    # Quiet-boot contract: silent when healthy, report when degraded
    for mid, reason in report.skipped.items():
        ctx.health.record_boot(mid, status="skipped", detail=reason)
    for mid, reason in report.quarantined.items():
        ctx.health.record_boot(mid, status="quarantined", detail=reason)
    problems = bool(report.skipped or report.quarantined or report.collisions)
    if problems or not quiet:
        print(f"[suijin boot] {report.summary()}")
    return ctx, report
