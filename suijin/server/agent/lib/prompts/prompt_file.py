"""prompt.md — the operator-editable system prompt.

The agent's system prompt is generated from code (role, methodology,
skills, tool catalog). This module makes it OPERATOR-EDITABLE with a
safe layout:

    ~/.suijin/workspace/prompt.md
    ┌ your prompt (edit freely — never touched by suijin)
    │ ...
    ├── <!-- suijin:dynamic-below (regenerated every engagement) -->
    └ dynamic state (tools available, KB status, packs, model) —
        APPENDED fresh at every engagement boot, never overwrites
        anything above the marker

Rules:
  - prompt.md is created at the FIRST engagement (and via `suijin prompt
    reset`) from the generated core; suijin never rewrites the user zone
  - the dynamic zone below the marker is regenerated at each boot from
    live state (tool availability changes, KB built, packs loaded)
  - edits apply at the NEXT engagement boot (a running engagement keeps
    its snapshot — the prompt is stable per run)
  - backups: every write snapshots to prompts/backups/ (last 10 kept);
    the pristine generated core is always at prompts/prompt.generated.md
  - a missing/corrupt file falls back to the generated core silently —
    a broken edit can never kill an engagement
"""

from __future__ import annotations

import contextlib
import re
import shutil
import time
from pathlib import Path

DYNAMIC_MARK = "<!-- suijin:dynamic-below — regenerated every engagement; keep your edits ABOVE this line -->"

_MAX_BACKUPS = 10


def _workspace() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR


def prompt_path() -> Path:
    return _workspace() / "prompt.md"


def _backups_dir() -> Path:
    d = _workspace() / "prompts" / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _generated_path() -> Path:
    d = _workspace() / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    return d / "prompt.generated.md"


def _snapshot(src: Path, tag: str) -> None:
    with contextlib.suppress(Exception):
        if src.is_file():
            dst = _backups_dir() / f"{tag}_{int(time.time())}_{src.name}"
            shutil.copy2(src, dst)
            keep = sorted(_backups_dir().glob(f"{tag}_*"), reverse=True)[:_MAX_BACKUPS]
            for old in _backups_dir().glob(f"{tag}_*"):
                if old not in keep:
                    with contextlib.suppress(OSError):
                        old.unlink()


def generated_core() -> str:
    """The static system-prompt core as code generates it (role,
    methodology, skills, tool catalog intro). The USER zone starts as a
    copy of this; the generated original is always preserved for reset
    and diff."""
    from suijin.modules.agent.lib.prompts.base import _static_prompt_core

    return _static_prompt_core()


def render_dynamic(config: dict | None = None) -> str:
    """The dynamic state block — regenerated from LIVE state at every
    engagement boot: which tools actually answer, KB status, packs,
    model. This is what makes a resumed run's prompt match reality."""
    cfg = dict(config or {})
    lines = ["## RUNTIME STATE (generated — do not edit)", ""]
    prov = str(cfg.get("provider") or "unknown")
    model = str(cfg.get(f"{prov}_model") or "") or str(cfg.get("model") or "")
    lines.append(f"- provider: {prov} · model: {model}")
    # tool availability: the binaries that gate the heavy hitters
    try:
        import shutil as _sh

        bins = ["nmap", "sqlmap", "gobuster", "ffuf", "nuclei", "hydra", "john"]
        have = [b for b in bins if _sh.which(b)]
        missing = [b for b in bins if not _sh.which(b)]
        if have:
            lines.append("- native tools available: " + ", ".join(have))
        if missing:
            lines.append("- native tools MISSING (use python/http alternatives): " + ", ".join(missing))
    except Exception:  # noqa: BLE001
        pass
    # KB
    try:
        from suijin.modules.platform.lib.workspace import WORKSPACE_DIR as _WS

        kb = _WS.parent / "suijin" / "kb.sqlite3"
        lines.append(
            f"- knowledge base: {'BUILT (search_kb live)' if kb.is_file() else 'DISABLED (operator must run: suijin pull kb)'}"
        )
    except Exception:  # noqa: BLE001
        pass
    # module packs
    try:
        from suijin.modules.loader import loaded_modules

        names = [m for m in loaded_modules()]
        lines.append(f"- module packs loaded: {len(names)}" + (f" ({', '.join(names[:8])})" if names else ""))
    except Exception:  # noqa: BLE001
        pass
    lines.append("")
    return "\n".join(lines)


def _write_initial(prompt_md: Path, core: str, dynamic: str) -> None:
    _snapshot(prompt_md, "pre_write")  # safety net for the rare rewrite case
    prompt_md.write_text(core.rstrip() + "\n\n" + DYNAMIC_MARK + "\n\n" + dynamic, encoding="utf-8")


def _split_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """(preamble, [(header_key, section_text)]) — sections cut at '## '
    headers (the core's own structure). header_key is normalized."""
    lines = text.splitlines()
    pre: list[str] = []
    sections: list[tuple[str, str]] = []
    cur_key: str = ""
    cur: list[str] = []
    for ln in lines:
        if ln.startswith("## "):
            if cur_key:
                sections.append((cur_key, "\n".join(cur).strip()))
            cur_key = re.sub(r"[^a-z0-9]+", "-", ln[3:].strip().lower()).strip("-")
            cur = [ln]
        elif cur_key:
            cur.append(ln)
        else:
            pre.append(ln)
    if cur_key:
        sections.append((cur_key, "\n".join(cur).strip()))
    return "\n".join(pre).strip(), sections


def overlay_prompt(core: str, user_zone: str) -> str:
    """The LAYERED prompt contract (2026-09-27 redesign).

    The user zone used to REPLACE the generated core wholesale — every
    doctrine fix had to land in two places or the operator's frozen copy
    silently diverged (it had). Now the core stays canonical and the
    operator's file is a set of named-section OVERRIDES:
      - a user '## header' matching a core section header REPLACES it
      - a user '## header' with no core counterpart APPENDS (new doctrine)
      - prose with no '## ' headers at all appends as OPERATOR NOTES
    Their words still outrank the core where they choose to speak; the
    rest of the core updates with the code."""
    if not str(user_zone or "").strip():
        return core
    pre_core, core_secs = _split_sections(core)
    pre_user, user_secs = _split_sections(user_zone)
    if not user_secs:
        notes = user_zone.strip()
        return core.rstrip() + "\n\n## OPERATOR NOTES\n" + notes + "\n"
    core_map = {k: t for k, t in core_secs}
    order = [k for k, _ in core_secs]
    out_secs = list(core_secs)
    for k, t in user_secs:
        if k in core_map:
            out_secs = [(k, t) if kk == k else (kk, tt) for kk, tt in out_secs]
        else:
            order.append(k)
            out_secs.append((k, t))
    parts = []
    if pre_core.strip():
        parts.append(pre_core)
    # the operator's preamble rides FIRST — but a copy of the core's own
    # preamble (which `reset` writes into the file) must not double it
    if pre_user.strip() and pre_user.strip() != pre_core.strip():
        parts.append(pre_user)
    parts += [t for _k, t in out_secs]
    return "\n\n".join(p for p in parts if p.strip()) + "\n"


def boot(config: dict | None = None) -> str:
    """Engagement-boot entry: ensure the editable file exists, refresh the
    DYNAMIC zone in place (user zone untouched), snapshot backups, and
    return the resolved prompt base (user zone + fresh dynamic). Called
    once per engagement; a running engagement keeps this snapshot."""
    p = prompt_path()
    core = generated_core()
    dynamic = render_dynamic(config)
    # the pristine generated core is ALWAYS on disk (reset + diff source)
    with contextlib.suppress(Exception):
        _generated_path().write_text(core, encoding="utf-8")
    try:
        if not p.is_file():
            _write_initial(p, core, dynamic)
            return core + "\n\n" + DYNAMIC_MARK + "\n\n" + dynamic
        text = p.read_text(encoding="utf-8", errors="replace")
        if DYNAMIC_MARK not in text:
            if len(text.strip()) < 40:
                # gutted marker-less file: regenerate (the operator's own
                # text, if any, is in the backups)
                _snapshot(p, "pre_fallback")
                _write_initial(p, core, dynamic)
                return core + "\n\n" + DYNAMIC_MARK + "\n\n" + dynamic
            # user file predates the marker (or was hand-stripped): append
            # the marker + fresh dynamic WITHOUT touching their text
            _snapshot(p, "pre_mark")
            p.write_text(text.rstrip() + "\n\n" + DYNAMIC_MARK + "\n\n" + dynamic, encoding="utf-8")
            user_zone_text = text
        else:
            user_zone_text = text.split(DYNAMIC_MARK, 1)[0]
            if not user_zone_text.strip() or len(user_zone_text.strip()) < 40:
                # a gutted user zone falls back to the generated core (a prompt
                # this short breaks the agent; the operator's own text is in
                # the backups if this was an accident)
                _snapshot(p, "pre_fallback")
                _write_initial(p, core, dynamic)
                return core + "\n\n" + DYNAMIC_MARK + "\n\n" + dynamic
            # refresh ONLY the dynamic zone on disk; the operator's sections
            # stay exactly as they edited them
            refreshed = user_zone_text.rstrip() + "\n\n" + DYNAMIC_MARK + "\n\n" + dynamic
            if refreshed != text:
                p.write_text(refreshed, encoding="utf-8")
        # LAYERED RESOLUTION: the operator's sections overlay the canonical
        # core (replace-by-name / append / OPERATOR NOTES) — the file never
        # freezes the core again. See overlay_prompt.
        resolved = overlay_prompt(core, user_zone_text)
        return resolved + "\n\n" + DYNAMIC_MARK + "\n\n" + dynamic
    except Exception:  # noqa: BLE001 — the prompt file can never kill a run
        with contextlib.suppress(Exception):
            _write_initial(p, core, dynamic)
        return core + "\n\n" + DYNAMIC_MARK + "\n\n" + dynamic


def reset() -> str:
    """Regenerate prompt.md from the current code-generated core (the old
    file is backed up first). Ships with the EDITING GUIDE so the layered
    contract is visible where the operator edits."""
    p = prompt_path()
    _snapshot(p, "pre_reset")
    guide = (
        "## OPERATOR EDITING GUIDE\n"
        "This file OVERLAYS the generated core — it no longer replaces it.\n"
        "- Re-write any '## section' below with the SAME header: your text replaces that section.\n"
        "- Add a NEW '## section' header: it appends as additional doctrine.\n"
        "- Plain prose without a '## ' header rides at the top as OPERATOR preamble.\n"
        "- Sections you delete simply fall back to the generated canonical version.\n"
        "- The zone below the dynamic marker is regenerated every engagement — never edit it.\n"
        "- `suijin prompt diff` shows your sections against the current generated core."
    )
    _write_initial(p, generated_core() + "\n\n" + guide, render_dynamic(None))
    return str(p)


def user_zone(path: Path | None = None) -> str:
    """The operator's editable zone (text above the marker)."""
    p = path or prompt_path()
    with contextlib.suppress(Exception):
        text = p.read_text(encoding="utf-8", errors="replace")
        return text.split(DYNAMIC_MARK, 1)[0]
    return ""


def split_zones(text: str) -> tuple[str, str]:
    """(user zone, dynamic zone) of a prompt.md body."""
    if DYNAMIC_MARK in text:
        u, d = text.split(DYNAMIC_MARK, 1)
        return u, d
    return text, ""


_MARK_RX = re.compile(re.escape(DYNAMIC_MARK))
_OVERRIDE_RX = re.compile(r"^\s*@suijin\s+override\s+([a-z0-9_-]+)", re.MULTILINE)

#: overrides the operator may declare in the user zone — their words
#: outrank the hardcoded checks (declared: "@suijin override <name>")
KNOWN_OVERRIDES = ("completion-gate", "guardrails")


def collect_overrides(text: str) -> list[str]:
    """Directive lines in the user zone: @suijin override <name>. The
    operator's prompt is the top authority — when they say a hardcoded
    gate should not fire, it does not fire (names are a fixed set so a
    typo can't silently disable something unknown)."""
    found = [m.group(1).strip().lower() for m in _OVERRIDE_RX.finditer(text or "")]
    return [o for o in found if o in KNOWN_OVERRIDES]
