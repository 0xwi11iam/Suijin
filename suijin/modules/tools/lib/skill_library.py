"""skill_library — the searchable methodology library (Wave A).

Imports the 125 WSTG + 16 attack-methodology SKILL.md files from the
CyberStrike AGPL repo into ~/.suijin/workspace/skills/ (data, never the
vendored tree). Inverted-index search (name/tag/owasp_id/category);
skill_load returns one file's body lazily (context conservation).

The AGENT uses this: the think context surfaces 'skill_library: N
methodologies loaded — use skill_search("sqli") before attacking' and
the tester doctrines reference specific skill IDs.
"""

from __future__ import annotations

import re
from pathlib import Path

_INDEX: dict | None = None

# AGPL attribution — required by the license
_ATTRIBUTION = (
    "# Source: CyberStrike (https://cyberstrike.io) — AGPL v3\n"
    "# Imported into Suijin under the same license terms.\n\n"
)


def _skills_dir() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    d = WORKSPACE_DIR / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def import_skills(force: bool = False) -> str:
    """Copy WSTG + attack-* SKILL.md files into the workspace library.
    Idempotent (skips already-present files unless force=True)."""
    try:
        src_root = Path(__file__).resolve().parents[3] / ".." / "references" / "CyberStrike" / ".cyberstrike" / "skill"
        if not src_root.is_dir():
            return "Error: skill source not found (references/CyberStrike/.cyberstrike/skill/)"
        dst = _skills_dir()
        count = 0
        # WSTG files
        wstg = src_root / "WEB" / "OWASP_WSTG_4.2"
        if wstg.is_dir():
            for f in wstg.glob("*/SKILL.md"):
                target = dst / f.parent.name / "SKILL.md"
                if force or not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(_ATTRIBUTION + f.read_text(encoding="utf-8", errors="ignore"))
                    count += 1
        # attack-* directories
        for d in sorted(src_root.iterdir()):
            if d.is_dir() and d.name.startswith("attack-"):
                for f in d.glob("SKILL.md"):
                    target = dst / d.name / "SKILL.md"
                    if force or not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(_ATTRIBUTION + f.read_text(encoding="utf-8", errors="ignore"))
                        count += 1
        _build_index()
        total = len(list(dst.glob("*/SKILL.md")))
        return f"skill library: {total} methodologies ({count} newly imported)"
    except Exception as e:  # noqa: BLE001
        return f"Error: import failed: {e}"


def _parse_frontmatter(text: str) -> dict:
    """Parse YAML-ish frontmatter from a SKILL.md file."""
    meta = {}
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if m:
        for line in m.group(1).split("\n"):
            if ":" in line:
                k, _, v = line.partition(":")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if v.startswith("[") and v.endswith("]"):
                    v = [x.strip().strip('"').strip("'") for x in v[1:-1].split(",") if x.strip()]
                meta[k] = v
    return meta


def _build_index() -> None:
    global _INDEX
    entries = []
    for f in sorted(_skills_dir().glob("*/SKILL.md")):
        text = f.read_text(encoding="utf-8", errors="ignore")
        meta = _parse_frontmatter(text)
        entries.append({
            "id": f.parent.name,
            "name": meta.get("name", f.parent.name),
            "description": meta.get("description", ""),
            "category": meta.get("category", ""),
            "owasp_id": meta.get("owasp_id", ""),
            "tags": meta.get("tags", []) if isinstance(meta.get("tags"), list) else [],
            "path": str(f),
            "_text": text.lower()[:2000],  # for full-text search
        })
    _INDEX = {"entries": entries}


def _get_index() -> dict:
    global _INDEX
    if _INDEX is None:
        _build_index()
    return _INDEX


def skill_search(query: str = "", limit: int = 10) -> str:
    """Search the methodology library. Returns matching skill IDs with
    descriptions — use skill_load(id) to read the full methodology."""
    try:
        if not query.strip():
            # summary when no query
            idx = _get_index()
            cats = {}
            for e in idx["entries"]:
                c = e["category"] or "uncategorized"
                cats[c] = cats.get(c, 0) + 1
            lines = [f"skill library: {len(idx['entries'])} methodologies loaded"]
            lines.extend(f"  {c}: {n}" for c, n in sorted(cats.items()))
            lines.append('search: skill_search("sqli") · load: skill_load("wstg-inpv-05")')
            return "\n".join(lines)
        q = query.lower().strip()
        idx = _get_index()
        scored = []
        for e in idx["entries"]:
            score = 0
            if q in e["name"].lower():
                score += 100
            if q in e["description"].lower():
                score += 50
            if q in str(e["tags"]).lower():
                score += 40
            if q in e["owasp_id"].lower():
                score += 30
            if q in e["category"].lower():
                score += 20
            if q in e["_text"]:
                score += 10
            if score > 0:
                scored.append((score, e))
        scored.sort(key=lambda x: -x[0])
        results = scored[: limit]
        if not results:
            return f"No methodologies match '{query}'. Try: sqli, xss, ssrf, jwt, auth, upload, graphql, race"
        lines = [f"skill search '{query}' — {len(scored)} match(es), top {len(results)}:"]
        for score, e in results:
            desc = e["description"][:80] if e["description"] else e["name"]
            lines.append(f"  {e['id']} [{e['owasp_id'] or e['category']}] — {desc}")
        lines.append(f"load full methodology: skill_load(\"{results[0][1]['id']}\")")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"Error: skill_search failed: {e}"


def skill_load(skill_id: str = "") -> str:
    """Load one methodology's full body (lazy — one at a time)."""
    try:
        if not skill_id.strip():
            return "Error: skill_id required (e.g. 'wstg-inpv-05' or 'attack-jwt')"
        safe = re.sub(r"[^a-z0-9_-]", "", skill_id.lower().strip())
        f = _skills_dir() / safe / "SKILL.md"
        if not f.exists():
            return f"Error: skill '{safe}' not found — use skill_search to find IDs"
        text = f.read_text(encoding="utf-8", errors="ignore")
        # strip the frontmatter for the agent (keep the body)
        body = re.sub(r"^# .*?Source:.*?\n\n", "", text, count=1, flags=re.DOTALL)
        return body[:8000]  # bounded — one skill per context load
    except Exception as e:  # noqa: BLE001
        return f"Error: skill_load failed: {e}"
