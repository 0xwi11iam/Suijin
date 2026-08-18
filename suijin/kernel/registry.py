"""Kernel registry — manifest parsing, dependency DAG, classification.

Understands CATEGORIES of trees (healthy / missing-dep / circular /
collision / broken) and produces a BootReport the controller acts on:
core problems abort, everything else degrades gracefully. Stdlib only.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from suijin.kernel.contracts import Tier

logger = logging.getLogger("suijin.kernel.registry")


@dataclass(eq=False)
class Unit:
    """One resolved module: its manifest plus classification.

    eq=False: identity semantics (hashable by object id) — units are dict
    keys throughout resolution; two manifests for the same id are handled
    explicitly by the collision policy, never by __eq__.
    """

    id: str
    version: str
    tier: Tier
    requires: list[str]
    provides: list[str]
    entry: str
    overrides: list[str]
    permissions: list[str]
    source: Path | None = None
    config: dict = field(default_factory=dict)
    broken_reason: str | None = None  # set when quarantined

    @classmethod
    def from_manifest(cls, data: dict, source: Path | None = None) -> "Unit":
        try:
            return cls(
                id=str(data["id"]),
                version=str(data.get("version", "0.0.0")),
                tier=Tier.from_string(data.get("tier", "recommended")),
                requires=[str(r) for r in data.get("requires", [])],
                provides=[str(p) for p in data.get("provides", [])],
                entry=str(data.get("entry", "")),
                overrides=[str(o) for o in data.get("overrides", [])],
                permissions=[str(p) for p in data.get("permissions", [])],
                source=source,
                config=dict(data.get("config", {})),
            )
        except (KeyError, ValueError) as e:
            unit = cls(
                id=str(data.get("id", "unknown")),
                version="?",
                tier=Tier.INSTALLED,
                requires=[],
                provides=[],
                entry="",
                overrides=[],
                permissions=[],
                source=source,
                broken_reason=f"invalid manifest: {e}",
            )
            return unit


@dataclass
class BootReport:
    """The scene analysis: what boots, what skips, what broke, in what order."""

    units: dict[str, Unit] = field(default_factory=dict)
    boot_order: list[Unit] = field(default_factory=list)
    bootable: set[str] = field(default_factory=set)
    skipped: dict[str, str] = field(default_factory=dict)  # id -> reason
    quarantined: dict[str, str] = field(default_factory=dict)
    collisions: list[tuple[str, str]] = field(default_factory=list)  # (id, losing tier)
    overridden: list[str] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str = ""

    def summary(self) -> str:
        if self.aborted:
            return f"BOOT ABORTED: {self.abort_reason}"
        n = len(self.bootable)
        parts = [f"{n} module(s) loaded"]
        if self.skipped:
            parts.append(
                f"{len(self.skipped)} skipped: "
                + ", ".join(f"{k} ({v.split(':')[0]})" for k, v in self.skipped.items())
            )
        if self.quarantined:
            parts.append(f"{len(self.quarantined)} quarantined: " + ", ".join(self.quarantined))
        if self.collisions:
            parts.append(f"{len(self.collisions)} collision(s) resolved (later tier lost)")
        return " · ".join(parts)


class Registry:
    """Collects manifests from multiple sources, resolves one DAG."""

    def __init__(self) -> None:
        self._units: dict[str, Unit] = {}
        # direct adds with the SAME id from different sources coexist here
        # until resolve() applies the collision policy (scan() replaces —
        # later source wins — because same-id across sources is an update)
        self._extra_units: list[Unit] = []

    # ── discovery ───────────────────────────────────────────────────

    def scan(self, root: Path) -> set[str]:
        """Discover plugin.json trees under root (one level of nesting).
        Later scans replace same-id units (later source wins)."""
        found: set[str] = set()
        root = Path(root)
        if not root.is_dir():
            return found
        for mf in sorted(root.glob("*/plugin.json")):
            try:
                data = json.loads(mf.read_text())
            except (OSError, ValueError) as e:
                unit = Unit.from_manifest({"id": mf.parent.name}, source=mf.parent)
                unit.broken_reason = f"unparseable manifest: {e}"
                self._units[unit.id] = unit  # id may be dir name; flagged broken
                found.add(unit.id)
                continue
            unit = Unit.from_manifest(data, source=mf.parent)
            self._units[unit.id] = unit  # replacement semantics: later wins
            found.add(unit.id)
        return found

    def add_manifest(self, data: dict, source: Path | None = None) -> None:
        unit = Unit.from_manifest(data, source)
        if unit.id in self._units:
            self._extra_units.append(unit)  # collision resolved at resolve()
        else:
            self._units[unit.id] = unit

    # ── resolution ──────────────────────────────────────────────────

    def resolve(self) -> BootReport:
        report = BootReport(units=dict(self._units))

        # 1. quarantine broken manifests outright
        live: dict[str, Unit] = {}
        for uid, unit in self._units.items():
            if unit.broken_reason:
                report.quarantined[uid] = unit.broken_reason
            else:
                live[uid] = unit
        # direct-add duplicates get a synthetic key so the collision policy
        # (step 2, keyed by real id) sees both candidates
        for i, unit in enumerate(self._extra_units):
            if unit.broken_reason:
                report.quarantined[unit.id] = unit.broken_reason
            else:
                live[f"{unit.id}#dup{i}"] = unit

        # 2. collision policy per id across tiers: lowest tier wins unless a
        #    higher-tier unit declares that id in overrides
        by_id: dict[str, list[Unit]] = {}
        for unit in live.values():
            by_id.setdefault(unit.id, []).append(unit)
        winners: dict[str, Unit] = {}
        for uid, candidates in by_id.items():
            candidates.sort(key=lambda u: u.tier.value)
            winner = candidates[0]
            for cand in candidates[1:]:
                if uid in cand.overrides:
                    winner = cand
                    report.overridden.append(uid)
                else:
                    loser_tier = cand.tier.name.lower()
                    report.collisions.append((uid, loser_tier))
            winners[uid] = winner

        # 3. cycle detection (Tarjan-lite via DFS coloring) over winners
        WHITE, GREY, BLACK = 0, 1, 2
        color = {uid: WHITE for uid in winners}
        stack: list[str] = []

        def dfs(uid: str) -> list[str] | None:
            color[uid] = GREY
            stack.append(uid)
            for dep in winners[uid].requires:
                if dep not in winners:
                    continue  # missing handled later
                if color[dep] == GREY:
                    i = stack.index(dep)
                    return stack[i:] + [dep]  # the cycle, named
                if color[dep] == WHITE:
                    cyc = dfs(dep)
                    if cyc:
                        return cyc
            stack.pop()
            color[uid] = BLACK
            return None

        cycles: dict[str, list[str]] = {}
        for uid in list(winners):
            if color[uid] == WHITE:
                cyc = dfs(uid)
                if cyc:
                    for member in cyc:
                        cycles[member] = cyc

        # 4. availability: a unit is bootable iff all requires are bootable
        #    and it's not in a cycle. Iterate to fixpoint.
        bootable: set[str] = set()
        skipped: dict[str, str] = {}
        changed = True
        pending = dict(winners)
        while changed:
            changed = False
            for uid, unit in list(pending.items()):
                if uid in cycles:
                    skipped[uid] = f"dependency cycle: {' -> '.join(cycles[uid])}"
                    del pending[uid]
                    changed = True
                    continue
                missing = [d for d in unit.requires if d not in winners]
                if missing:
                    skipped[uid] = f"missing dependency: {', '.join(missing)}"
                    del pending[uid]
                    changed = True
                    continue
                unready = [d for d in unit.requires if d not in bootable and d in pending]
                if unready:
                    continue  # may become ready next pass
                bootable.add(uid)
                del pending[uid]
                changed = True
        # anything still pending has a skipped/cyclic dependency chain
        for uid, unit in pending.items():
            blocked = [d for d in unit.requires if d not in bootable]
            skipped[uid] = f"dependencies unavailable: {', '.join(blocked)}"

        # 5. core-missing aborts the boot
        core_problems = []
        for uid, unit in winners.items():
            if unit.tier is Tier.CORE and uid in skipped:
                core_problems.append(f"{uid} ({skipped[uid]})")
        if core_problems:
            report.aborted = True
            report.abort_reason = "core module(s) unavailable: " + "; ".join(core_problems)
            report.skipped = skipped
            return report

        # 6. topological boot order over bootable units
        order: list[Unit] = []
        placed: set[str] = set()
        while len(placed) < len(bootable):
            progressed = False
            for uid in sorted(bootable - placed):
                if all(d in placed for d in winners[uid].requires):
                    order.append(winners[uid])
                    placed.add(uid)
                    progressed = True
            if not progressed:  # defensive — cycles were already removed
                break

        report.units = winners
        report.boot_order = order
        report.bootable = bootable
        report.skipped = skipped
        return report
