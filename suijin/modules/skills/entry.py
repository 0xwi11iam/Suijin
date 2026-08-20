"""Skills module — pure-markdown drop-in skills.

Any .md file under suijin/skills/ (shipped in the wheel) becomes agent
knowledge at boot: the module scans the drop root, enforces per-file
and total budgets, and exposes the merged text as the 'skills.docs'
service. prompts/base.py injects it as a dedicated SKILLS section —
no manifest, no code, drop a file and reboot.
"""

from __future__ import annotations

from pathlib import Path

from suijin.kernel.contracts import Module, Tier

MAX_FILE_BYTES = 8 * 1024
MAX_TOTAL_BYTES = 64 * 1024


def _drop_roots() -> list[Path]:
    """Bundled package drop root (wheel-shipped)."""
    return [Path(__file__).resolve().parents[2] / "skills"]  # suijin/skills/


def scan_drop_skills(roots: list[Path] | None = None) -> tuple[str, list[str]]:
    """Merge every .md under roots into prompt text.

    Returns (text, skipped) — skipped lists files that exceeded budget.
    """
    roots = roots if roots is not None else _drop_roots()
    parts: list[str] = []
    skipped: list[str] = []
    total = 0
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(sorted(root.glob("*.md")))
    for f in files:
        try:
            body = f.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            continue
        if not body or body.startswith(("<!-- skip", "<!--skip")):
            continue
        if len(body.encode("utf-8")) > MAX_FILE_BYTES:
            skipped.append(f.name)
            continue
        if total + len(body) > MAX_TOTAL_BYTES:
            skipped.append(f.name)
            continue
        title = f.stem.replace("_", " ").replace("-", " ").title()
        parts.append(f"### Skill: {title}\n{body}")
        total += len(body)
    text = "\n\n".join(parts)
    return text, skipped


class PackModule(Module):
    id = "skills"
    tier = Tier.RECOMMENDED

    def register(self, ctx) -> None:
        ctx.register_service("skills.docs", lambda: scan_drop_skills()[0])

    def start(self, ctx) -> None:
        if not ctx.has_tool("skill_read"):
            ctx.register_tool(
                "skill_read",
                lambda args, _ctx: read_skill(args.get("pack", "")),
                description="Fetch a pack's full usage guide (see the skill index for names).",
                owner="skills",
                params=["pack"],
            )
        _text, skipped = scan_drop_skills()
        n = len(_text.split("### Skill:")) - 1 if _text else 0
        ctx.journal.append(
            "skills",
            f"{n} drop-in skill(s) loaded" + (f" (skipped oversized: {', '.join(skipped)})" if skipped else ""),
        )

    def stop(self, ctx) -> None:
        pass


# ── G48: skill decay pruning ───────────────────────────────────────────


def decay_report() -> str:
    """Flag drop-in skills never referenced in any engagement audit."""
    import re as _re

    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    # gather every tool word the agent actually used across trails
    used = set()
    trails = WORKSPACE_DIR / "outputs" / "audit_trails"
    if trails.is_dir():
        for p in trails.glob("*.json"):
            try:
                body = p.read_text(errors="ignore").lower()
            except OSError:
                continue
            used.update(_re.findall(r"[a-z_]{4,}", body))
    roots = _drop_roots()
    stale = []
    for root in roots:
        if not root.is_dir():
            continue
        for f in root.glob("*.md"):
            head = f.read_text(errors="ignore")[:400].lower()
            keywords = [
                w for w in _re.findall(r"[a-z_]{4,}", head) if w not in ("skill", "markdown", "skip", "notes", "this")
            ]
            if keywords and not any(k in used for k in keywords):
                stale.append(f.name)
    if not stale:
        return "No stale skills detected."
    return "possibly stale (no keyword overlap with engagement history):\n  " + "\n  ".join(stale[:10])


# ── on-demand skill docs: index in the prompt, full text on request ──


def skill_index() -> str:
    """One line per pack that ships a skill.md (the agent fetches detail
    via skill_read instead of every doc living in the prompt)."""
    from suijin.modules.loader import discover_modules, get_loaded_modules

    discover_modules()
    lines = []
    for key, mod in sorted((get_loaded_modules() or {}).items()):
        if mod.get("skill"):
            lines.append(key)
    if not lines:
        return ""
    return "packs with detailed usage guides (fetch with skill_read): " + ", ".join(lines[:80])


def read_skill(pack: str = "") -> str:
    """Return a pack's full skill.md (or the closest match)."""
    from suijin.modules.loader import discover_modules, get_loaded_modules

    if not pack.strip():
        return "Error: pack name required (see the skill index for names)"
    discover_modules()
    mods = get_loaded_modules() or {}
    name = pack.strip().lower()
    hit = next((k for k in mods if k.lower() == name), None)
    if hit is None:
        import difflib

        close = difflib.get_close_matches(name, [k.lower() for k in mods], n=1)
        if close:
            hit = next(k for k in mods if k.lower() == close[0])
    if hit is None:
        avail = ", ".join(sorted(mods)[:12])
        return f"Error: no pack skill named '{pack}' (have: {avail}...)"
    doc = mods[hit].get("skill") or ""
    return f"# {hit}\n{doc[:8000]}" if doc else f"'{hit}' has no skill doc"
