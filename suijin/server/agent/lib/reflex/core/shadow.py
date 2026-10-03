"""Shadow mode — the outcome-labeled corpus that makes judgment empirical.

Every reflex decision can be shadowed: run BOTH the classifier and the
legacy path, log features + both answers + the turn, then close the
loop later with `record_outcome` (did the next-N turns actually
improve?). Two payoffs:
- the label set a System One model trains on (the legacy LLM's answers
  over the SAME features are free labels while they're still smarter)
- intervention PRECISION — the fraction of nudges followed by real
  improvement — the number that proves the supervisor stopped
  bullshitting, computable before and after the swap

Storage: workspace/reflex-shadow.jsonl (append-only, operator-local).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

_PATH: Path | None = None


def _shadow_path() -> Path:
    global _PATH
    if _PATH is not None:
        return _PATH
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    _PATH = Path(WORKSPACE_DIR) / "reflex-shadow.jsonl"
    return _PATH


def set_shadow_path(p: Path | None) -> None:
    """Tests pin their own file; None resets."""
    global _PATH
    _PATH = p


def shadow_log(
    qid: str,
    features: dict,
    classifier: dict | None,
    legacy: str | None,
    acted: bool,
    turn: int,
) -> None:
    """One decision record. Never raises — shadow is measurement, not load."""
    try:
        p = _shadow_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "t": round(time.time(), 3),
            "turn": int(turn),
            "qid": str(qid),
            "features": {
                k: v for k, v in dict(features or {}).items() if isinstance(v, (int, float, str, bool, type(None)))
            },
            "classifier": classifier,  # {"choice","p","engine","ms"} or None
            "legacy": str(legacy or "")[:200],  # what today's path said/did
            "acted": bool(acted),
            "outcome": None,  # closed by record_outcome
        }
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:  # noqa: BLE001 — never break a turn over telemetry
        pass


def record_outcome(turn: int, improved: bool, lookback: int = 6) -> int:
    """Close the outcome loop for recent unclosed records at `turn`.
    `improved` = the seam's verdict that the last-N turns got better
    (iterations-since-finding shrank, phase advanced, chain confirmed).
    Returns records closed."""
    p = _shadow_path()
    if not p.is_file():
        return 0
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        out, closed = [], 0
        for ln in lines:
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            if rec.get("outcome") is None and rec.get("acted") and 0 < turn - int(rec.get("turn") or 0) <= lookback:
                rec["outcome"] = bool(improved)
                closed += 1
            out.append(json.dumps(rec, default=str))
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
        tmp.replace(p)
        return closed
    except Exception:  # noqa: BLE001
        return 0


def intervention_precision() -> float | None:
    """acted records with closed outcomes: fraction improved. None when
    no closed records yet — the honest answer, not a fake zero."""
    p = _shadow_path()
    if not p.is_file():
        return None
    acted_total = improved = 0
    try:
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            if rec.get("acted") and rec.get("outcome") is not None:
                acted_total += 1
                improved += bool(rec["outcome"])
    except Exception:  # noqa: BLE001
        return None
    return round(improved / acted_total, 3) if acted_total else None
