"""Chain engine — the harness holds the chain so the model doesn't have to.

The operator's diagnosis (2026-10-29): the AI knows syntax but can't HOLD
a chain — tool results evict the plan from working memory within a turn
or two. Humans carry "XSS → steal token → fuzz → SQLi → RCE → internal →
privesc" effortlessly across hours.

Three mechanisms:
  1. INDICATOR FIRING — tool results are scanned for indicators (iframe,
     openapi.json, JWT, file upload, .git/…) and matching chain templates
     surface AT OBSERVATION TIME (the mechanized "organic" association)
  2. TEMPLATES — precondition graphs, not scripts: each node says what
     PRIMITIVE unlocks it; assembly becomes a graph walk, not invention
  3. THE LOOM — chain_plan state re-rendered into CURRENT_STATE every
     turn; the chain is never more than one glance away

Templates are operator-tunable (workspace config/chain_templates.json)
with a neutral in-tree seed. Nothing engagement-specific is committed.
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

_SEED = Path(__file__).resolve().parent / "chain_templates_seed.json"


def load_templates() -> list[dict]:
    """Workspace templates win; the in-tree seed is the neutral fallback."""
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

        p = Path(WORKSPACE_DIR) / "config" / "chain_templates.json"
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
    with contextlib.suppress(Exception):
        return json.loads(_SEED.read_text(encoding="utf-8"))
    return []


def _catalogs() -> list[dict]:
    """ATT&CK + WSTG lookup catalogs (public frameworks, cleaned at import
    — see chain_catalog_manifest.json for provenance and rejects)."""
    out = []
    for name in ("attck", "wstg"):
        p = Path(__file__).resolve().parent / f"chain_catalog_{name}.json"
        with contextlib.suppress(Exception):
            data = json.loads(p.read_text(encoding="utf-8"))
            out.extend(data.get("entries", []))
    return out


def lookup(query: str, stage: str = "", limit: int = 8) -> str:
    """Search the curated templates + the ATT&CK/WSTG catalogs (815
    cleaned entries — the depth layer; indicators only fire the curated
    set, this searches everything)."""
    q = str(query or "").strip().casefold()
    if not q:
        return "chain_lookup(query='jwt|privesc|upload|…', stage='optional filter')"
    hits: list[tuple[int, dict]] = []
    # curated templates rank first
    for t in load_templates():
        hay = f"{t['id']} {t['name']} {t.get('trigger_hint', '')}".casefold()
        if q in hay:
            hits.append(
                (
                    100,
                    {
                        "id": t["id"],
                        "name": t["name"],
                        "stages": ["chain-template"],
                        "summary": t.get("trigger_hint", ""),
                        "source": "curated",
                    },
                )
            )
    for e in _catalogs():
        if stage and stage.casefold() not in " ".join(e.get("stages", [])).casefold():
            continue
        hay = f"{e['id']} {e['name']} {e.get('summary', '')}".casefold()
        if q in hay:
            hits.append((10, e))
    hits.sort(key=lambda h: -h[0])
    if not hits:
        return (
            f"No catalog hits for '{q}'. Try broader terms (injection, privilege, upload, "
            "session, ssrf) or stage='07-Injection' / 'privilege-escalation'."
        )
    lines = [f"chain catalog: {len(hits)} hit(s) for '{q}'" + (f" (stage {stage})" if stage else "")]
    for _, e in hits[: max(1, min(int(limit or 8), 20))]:
        src = e.get("source", "?")
        kind = "TEMPLATE chain_suggest('" + e["id"] + "')" if src == "curated" else e["id"]
        lines.append(f"- [{src}] {e['name']} ({kind}) — {e.get('summary', '')[:180]}")
    return "\n".join(lines)


def template_ids() -> list[str]:
    return [t.get("id", "?") for t in load_templates()]


def scan_indicators(text: str, already_fired: list[str] | None = None) -> tuple[str, list[str]]:
    """Fire matching templates into a tool result — the association surfaces
    at observation time, not when the model remembers to think.
    Returns (suggestion_lines, newly_fired_ids)."""
    if not text:
        return "", []
    fired = set(already_fired or [])
    lines: list[str] = []
    newly: list[str] = []
    for t in load_templates():
        tid = t.get("id", "")
        if tid in fired:
            continue
        for ind in t.get("indicators", []):
            with contextlib.suppress(Exception):
                if re.search(ind, text, re.I):
                    lines.append(
                        f"INDICATOR → chain template '{tid}' ({t.get('name', '')}) applies. "
                        f"{t.get('trigger_hint', '')} — call chain_suggest('{tid}')."
                    )
                    fired.add(tid)
                    newly.append(tid)
                    break
    return "\n".join(lines), newly


def suggest(template_id: str) -> str:
    """A template as an executable DRAFT the model reviews, edits, approves."""
    for t in load_templates():
        if t.get("id") == template_id:
            out = [f"CHAIN TEMPLATE: {t.get('name', template_id)}"]
            out.append(f"trigger: {t.get('trigger_hint', '')}")
            pre = t.get("preconditions", [])
            if pre:
                out.append("preconditions (confirm these primitives first):")
                for p in pre:
                    out.append(f"  - need: {p.get('need')} — how: {p.get('how')}")
            out.append("steps:")
            for i, s in enumerate(t.get("steps", []), 1):
                tool = s.get("tool", "?")
                note = s.get("note", "")
                args = json.dumps(s.get("args", {}), separators=(",", ":")) if s.get("args") else ""
                out.append(f"  {i}. {tool}({args}) {('— ' + note) if note else ''}".rstrip())
            out.append(f"exit criteria: {t.get('exit', 'primitive confirmed or 2 failures')}")
            unl = t.get("unlocks", [])
            if unl:
                out.append(f"unlocks: {', '.join(unl)}")
            out.append(
                "Track progress: chain_track(template='" + template_id + "', action='start'). "
                "Confirm or kill nodes as you learn — the loom carries the chain so you don't have to."
            )
            return "\n".join(out)
    known = ", ".join(template_ids())
    return f"No chain template '{template_id}'. Known: {known}"


def track(chain_plan: dict | None, template_id: str, action: str, node: str = "", note: str = "") -> dict:
    """chain_track(action=start|confirm|dead) — advance the loom.

    'confirm' on a node marks every template it unlocks as available."""
    plan = dict(chain_plan or {})
    tpl = plan.setdefault(template_id, {"status": "suggested", "nodes": {}})
    nodes = tpl.setdefault("nodes", {})
    if action == "start":
        tpl["status"] = "active"
    elif action == "confirm":
        tpl["status"] = "active"
        if node:
            nodes[node] = {"status": "confirmed"}
    elif action == "dead":
        if node:
            nodes[node] = {"status": "dead"}
        else:
            tpl["status"] = "dead"
    if note:
        tpl["last_note"] = str(note)[:200]
    # propagation: a confirmed node unlocks dependent templates
    unlocked = []
    for t in load_templates():
        for p in t.get("preconditions", []):
            if node and p.get("need") == node:
                dep = plan.setdefault(t["id"], {"status": "available", "nodes": {}})
                if dep.get("status") not in ("active", "dead"):
                    dep["status"] = "available"
                    unlocked.append(t["id"])
                break
    if unlocked:
        tpl["unlocked"] = unlocked
    return plan


def loom_render(chain_plan: dict | None) -> str:
    """The CURRENT_STATE block — the chain, one glance, every turn."""
    plan = chain_plan or {}
    if not plan:
        return ""
    icon = {"suggested": "·", "available": "○", "active": "▶", "dead": "✗", "done": "✔", "confirmed": "✔"}
    lines = ["CHAIN LOOM:"]
    for tid, t in plan.items():
        if t.get("status") == "suggested" and not t.get("nodes"):
            lines.append(f"  {icon.get('suggested', '·')} {tid} (suggested — chain_suggest('{tid}')")
            continue
        st = t.get("status", "?")
        nodes = t.get("nodes", {})
        node_bits = [f"{n}:{icon.get(v.get('status', '?'), '?')}" for n, v in nodes.items()]
        tail = (" [" + " ".join(node_bits) + "]") if node_bits else ""
        note = f" — {t['last_note']}" if t.get("last_note") else ""
        lines.append(f"  {icon.get(st, '·')} {tid}{tail}{note}")
    return "\n".join(lines)
