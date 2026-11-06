"""prior-work gate — never re-derive a buried finding.

The 2026-11-05 field lesson: the swarm spent the night re-deriving the
operator's own ABANDONED finding from an October catalog (751 tool
calls to rediscover a documented dead end). The knowledge existed in
the system; nothing refused the intent.

This gate runs at intent CREATION: scan past engagement catalogs for
findings whose class/title matches the new intent, and refuse (or
require --force) when the intent re-derives something already worked.

Conservative by design: a match needs real token overlap with a
catalog title or an explicit class hit — vague overlap never blocks.
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "with", "via", "on", "in",
    "of", "to", "test", "probe", "audit", "check", "use", "using",
    "confirm", "verify", "chain", "bypass", "exploit", "write", "file",
    "note", "then", "complete", "engagement", "default",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]{3,}", str(text or "").lower())
    return {w for w in words if w not in STOPWORDS}


def _catalog_entries() -> list[dict]:
    """Every entry in every past engagement's exploit catalog."""
    out: list[dict] = []
    with contextlib.suppress(Exception):
        from suijin.server.platform.lib.workspace import WORKSPACE_DIR

        root = Path(WORKSPACE_DIR).parent / "engagements"
        for cat in root.glob("*/exploits/catalog.json"):
            with contextlib.suppress(Exception):
                data = json.loads(cat.read_text())
                entries = data.get("entries", {}) if isinstance(data, dict) else {}
                for eid, e in entries.items():
                    if not isinstance(e, dict):
                        continue
                    out.append(
                        {
                            "engagement": cat.parent.parent.name,
                            "id": eid,
                            "class": str(e.get("class", "")),
                            "title": str(e.get("title", "")),
                            "status": str(e.get("status", "")),
                            "target": str(e.get("target", "")),
                        }
                    )
    return out


def check_prior_work(intent_text: str, target: str = "") -> dict | None:
    """The matching prior-work entry, or None when clear.

    A match needs: a shared CLASS word (open_redirect, ssrf, xss…) AND
    ≥2 shared title tokens with a NON-fresh entry (abandoned/reported/
    verified = worked before). 'done' entries are fresh work, not
    burials — they never match.
    """
    tok_intent = _tokens(intent_text)
    if not tok_intent:
        return None
    worked_states = {"abandoned", "reported", "duplicate", "wont_fix", "verified"}
    best = None
    best_score = 0
    for e in _catalog_entries():
        if e["status"].lower() in ("", "done", "fresh"):
            continue
        # class words SPLIT on underscores — 'open_redirect' must match
        # intents that say 'open redirect' (found by the regression test:
        # the underscore-joined token never matched plain English)
        class_hit = bool(e["class"]) and any(
            c in tok_intent for c in re.findall(r"[a-z]+", e["class"].lower()) if len(c) > 3
        )
        title_tok = _tokens(e["title"])
        shared = tok_intent & title_tok
        score = len(shared) + (2 if class_hit else 0)
        if score >= 3 and score > best_score:  # ≥2 shared tokens + class, or strong title overlap
            best_score = score
            best = {**e, "shared_tokens": sorted(shared), "score": score}
    return best


def gate_intent(intent_text: str, target: str = "", force: bool = False) -> tuple[bool, str]:
    """(allowed, message). force=True bypasses with a note."""
    hit = check_prior_work(intent_text, target)
    if not hit:
        return True, ""
    if force:
        return True, (
            f"note: prior work matched ({hit['engagement']}/{hit['id']} "
            f"[{hit['status']}] {hit['title'][:70]}) — proceeding by operator force"
        )
    return False, (
        f"REFUSED — this looks like work already done: {hit['engagement']}/{hit['id']} "
        f"[{hit['status']}] {hit['title'][:90]}\n"
        f"matched: class={hit['class'] or '?'} shared={hit['shared_tokens']}\n"
        "re-deriving buried findings wastes the whole run (2026-11-05 field lesson). "
        "If this is genuinely new — different endpoint, different class, new chain — "
        "rephrase the intent to say what's NEW, or add --force."
    )
