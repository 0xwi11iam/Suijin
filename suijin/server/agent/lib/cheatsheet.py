"""The Dynamic Cheatsheet — persistent cross-engagement memory.

The problem it solves (operator analysis, 2026-10-02): Suijin discovered
the lab's gateway parses JWTs, burned 22 token candidates, got "invalid
token" every time — and moved on. The next engagement started from zero.
Every layer Suijin had (librarian, .sje resume, chain memory, the bench's
learnings) is EPISODE-SCOPED; nothing survives between engagements.

This is the research-backed fix (Dynamic Cheatsheet): concise, self-
curated, TRANSFERABLE snippets persisted at the workspace level —
"JWT gate with kid=primary: alg:none rejected, 21 weak secrets rejected,
HS256-with-public-PEM is the one that worked" — served into every think
context, written by the agent mid-run (a tool) and distilled
automatically when an engagement concludes.

Injection posture: snippets are agent-authored SUMMARIES derived from
hostile target output — a poisoned target can trick an agent into
writing an instruction into its own memory. The cheatsheet rides the
same _wrap_untrusted boundary as every other derived-from-target byte.

Storage: WORKSPACE_DIR/cheatsheet.json (operator-local, never committed).
Capped; oldest pruned. Same-seed determinism is unaffected (read-only in
the think path; writes are explicit).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from pathlib import Path

MAX_SNIPPETS = 40
MAX_NOTE_CHARS = 300
#: served into one think context (most recent first)
SERVE_LIMIT = 12

_cache: dict = {"mtime": 0.0, "data": None}


def _path() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return Path(WORKSPACE_DIR) / "cheatsheet.json"


def _load() -> list[dict]:
    p = _path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return []
    if _cache["data"] is None or mtime != _cache["mtime"]:
        with contextlib.suppress(Exception):
            _cache.update(mtime=mtime, data=json.loads(p.read_text(encoding="utf-8")))
        if _cache["data"] is None or not isinstance(_cache["data"], list):
            _cache.update(mtime=mtime, data=[])
    return _cache["data"]


def _save(rows: list[dict]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    _cache.update(mtime=p.stat().st_mtime, data=rows)


def _normalize(note: str) -> str:
    return re.sub(r"\s+", " ", str(note or "")).strip()[:MAX_NOTE_CHARS]


def add(note: str, tag: str = "", source: str = "") -> str:
    """Store one snippet. Dedupes on normalized text; returns the outcome."""
    n = _normalize(note)
    if not n:
        return "Error: empty note"
    rows = _load()
    if any(r.get("note") == n for r in rows):
        return "already known (deduped)"
    # provenance (2026-10-07): the origin target rides every snippet so
    # lab-derived entries can be scrubbed/excluded later — content stays
    # target-agnostic, origin never does
    try:
        from suijin.modules.platform.lib.workspace import engagement_dir as _ed

        origin = _ed().name
    except Exception:  # noqa: BLE001 — provenance is best-effort
        origin = ""
    rows.append(
        {
            "note": n,
            "tag": str(tag or "general")[:40],
            "source": str(source or "")[:60],
            "origin": origin[:80],
            "at": round(time.time(), 3),
        }
    )
    rows = rows[-MAX_SNIPPETS:]  # oldest pruned
    _save(rows)
    return f"stored ({len(rows)} total)"


def snippets() -> list[dict]:
    return list(_load())


def render_for_context() -> str:
    """The CHEATSHEET context section: the freshest transferable notes.
    Content is served RAW here — the think node wraps it untrusted."""
    rows = _load()[-SERVE_LIMIT:][::-1]
    if not rows:
        return ""
    out = []
    for i, r in enumerate(rows, 1):
        tag = str(r.get("tag", "general"))
        out.append(f"{i}. [{tag}] {r['note']}")
    return "\n".join(out)


# ── conclusion distillation ─────────────────────────────────────────────


def distill_prompt(trace_tail: str) -> str:
    """The one-shot distillation: dead batteries and proven techniques →
    transferable snippets. Phrased target-agnostically on purpose: the
    next engagement is a DIFFERENT target with maybe the same tech."""
    return f"""You are distilling transferable lessons from a finished security engagement.

From the trace below, extract 0-3 CONCISE snippets a FUTURE engagement
would benefit from. Rules:
- Transferable: about TECHNIQUES, tool behaviors, fingerprints, dead ends —
  not about this specific target's data.
- Dead batteries matter most: things that were tried and CANNOT work
  (state what was ruled out and why, so the next run skips it).
- Proven techniques matter equally: what WORKED, phrased as a playbook step.
- One snippet = one line, <=300 chars, imperative voice.
- If nothing transferable happened, return an empty list.

Respond with ONLY a JSON array of objects: [{{"tag": "jwt|sqli|recon|...", "note": "..."}}]

Trace (tail):
{trace_tail[:8000]}"""


def parse_distillation(text: str) -> list[dict]:
    """Best-effort JSON array extraction from the model's reply."""
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return []
    with contextlib.suppress(ValueError):
        out = json.loads(m.group(0))
        if isinstance(out, list):
            return [o for o in out if isinstance(o, dict) and str(o.get("note", "")).strip()][:3]
    return []


def distill_from_trace(trace_tail: str, generate_fn) -> int:
    """One LLM call at engagement conclusion. Never raises; returns stored count."""
    try:
        reply = str(generate_fn([{"role": "user", "content": distill_prompt(trace_tail)}], {}) or "")
    except Exception:  # noqa: BLE001 — distillation is best-effort by contract
        return 0
    stored = 0
    for o in parse_distillation(reply):
        if "stored" in add(str(o.get("note", "")), tag=str(o.get("tag", "general")), source="conclusion"):
            stored += 1
    return stored


def reset_cache() -> None:
    _cache.update(mtime=0.0, data=None)
