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
        _text, skipped = scan_drop_skills()
        n = len(_text.split("### Skill:")) - 1 if _text else 0
        ctx.journal.append(
            "skills",
            f"{n} drop-in skill(s) loaded" + (f" (skipped oversized: {', '.join(skipped)})" if skipped else ""),
        )

    def stop(self, ctx) -> None:
        pass
