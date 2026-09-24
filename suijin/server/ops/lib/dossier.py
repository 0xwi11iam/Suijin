"""Target dossiers — persistent per-target intelligence across engagements.

Merges every artifact that mentions a target into one profile:
- red knowledge graph constraints (blocks / WAF / verified CVEs / behavior)
- failure_db entries (what already failed and why)
- audit trail mentions (engagements that touched it, finding counts)
- report mentions (files referencing it)

CLI: `suijin dossier <target>`. Agent tool: `target_dossier(target)` — the
agent consults it before re-attacking a known target.
"""

from __future__ import annotations

import json
from pathlib import Path


def _workspace_dir():
    """Platform workspace dir (honours a monkeypatched module attr)."""
    v = globals().get("WORKSPACE_DIR")
    if v is not None:
        return v
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR


def _red_kg_path():
    v = globals().get("RED_KG_PATH")
    if v is not None:
        return v
    return _workspace_dir().parent / "suijin" / "modules" / "redteam" / "lib" / "intel" / "knowledge_graph.json"


def __getattr__(name):
    if name == "WORKSPACE_DIR":
        return _workspace_dir()
    if name == "RED_KG_PATH":
        return _red_kg_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_dossier(target: str, workspace: Path | None = None, red_kg: Path | None = None) -> dict:
    target = (target or "").strip().lower()
    if not target:
        raise ValueError("target required (IP, hostname, or URL)")
    ws = Path(workspace) if workspace else _workspace_dir()
    kg_path = Path(red_kg) if red_kg else _red_kg_path()
    d: dict = {"target": target}

    # red KG constraints — via the PUBLIC API (backend-agnostic: json or
    # neo4j). The explicit red_kg= file override stays for tests/fixed exports.
    constraints: dict[str, list] = {}
    try:
        if red_kg is not None:
            kg = json.loads(kg_path.read_text())
            node = kg.get(target) or kg.get(target.split("//")[-1].split("/")[0])
            if node:
                for ctype, rules in node.items():
                    if isinstance(rules, list):
                        constraints[ctype] = [r.get("rule", str(r))[:120] for r in rules if isinstance(r, dict)]
        else:
            from suijin.modules.redteam.lib.intel import knowledge_graph as red_kg_api

            for lookup in (target, target.split("//")[-1].split("/")[0]):
                cons = red_kg_api.get_constraints(lookup)
                if cons:
                    for ctype, rows in cons.items():
                        if isinstance(rows, list) and not ctype.startswith("_"):
                            constraints[ctype] = [r.get("rule", str(r))[:120] for r in rows if isinstance(r, dict)]
                    break
    except (OSError, ValueError):
        pass
    d["constraints"] = constraints

    # failure history
    failures: list[str] = []
    fdb = ws / "failure_db.json"
    if fdb.exists():
        try:
            for e in json.loads(fdb.read_text()):
                if target in str(e.get("target", "")).lower():
                    failures.append(f"{e.get('technique', '?')} — {e.get('reason', '?')[:80]}")
        except ValueError:
            pass
    d["failures"] = failures

    # audit mentions
    engagements: list[str] = []
    audit_dir = ws / "audit_trails"
    if audit_dir.is_dir():
        for f in sorted(audit_dir.glob("*.json")):
            try:
                blob = f.read_text().lower()
                if target in blob:
                    t = json.loads(f.read_text())
                    engagements.append(
                        f"{t.get('engagement', f.stem)} — {len(t.get('findings', []))} findings, "
                        f"{t.get('total_actions', 0)} actions"
                    )
            except (OSError, ValueError):
                continue
    d["engagements"] = engagements

    # report mentions
    reports: list[str] = []
    reports_dir = ws / "reports"
    if reports_dir.is_dir():
        for f in sorted(reports_dir.rglob("*")):
            if f.is_file() and f.suffix in (".md", ".json", ".html"):
                try:
                    if target in f.read_text(errors="ignore").lower():
                        reports.append(str(f.relative_to(ws)))
                except OSError:
                    continue
    d["reports"] = reports
    return d


def render_dossier(d: dict) -> str:
    lines = [f"# Dossier — {d['target']}", ""]
    lines.append("## Knowledge-graph constraints")
    if d["constraints"]:
        for ctype, rules in d["constraints"].items():
            lines.append(f"  {ctype}:")
            lines += [f"    - {r}" for r in rules[:5]]
    else:
        lines.append("  (none recorded)")
    lines.append("")
    lines.append("## Failed techniques (avoid repeating)")
    lines += [f"  - {f}" for f in d["failures"][:8]] or ["  (none)"]
    lines.append("")
    lines.append("## Engagement history")
    lines += [f"  - {e}" for e in d["engagements"][:8]] or ["  (first contact)"]
    lines.append("")
    lines.append("## Reports mentioning target")
    lines += [f"  - {r}" for r in d["reports"][:8]] or ["  (none)"]
    total = sum(len(d[k]) for k in ("constraints", "failures", "engagements", "reports"))
    lines.append(f"\nintel richness: {total} item(s) across 4 sources")
    return "\n".join(lines)
