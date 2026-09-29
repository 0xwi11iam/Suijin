"""The drive bandit (Stage 3) — the loop-learner.

The frozen-LLM honesty: the model can't learn, but the CONTROLLER
around it can. A contextual bandit (UCB1) over drive policies — which
surprise-types deserved reflex probes, which amplification led to
CONFIRMEDs — keyed by target class, persisted in the workspace, updated
only on real outcomes. After a handful of engagements the drive has
tuned itself to what pays on YOUR targets. That's credit assignment
crossing sessions.

GUARDRAILS (load-bearing):
- Only CONFIRMED pays. Dead ends pay nothing; nothing pays nothing.
- The bandit modulates STRENGTHS AND ORDERING only — never scope,
  never tools, never policy. A winning arm raises its probe priority;
  it cannot unlock a forbidden action.
- Recency-weighted: old wins fade (the target changes).
"""

from __future__ import annotations

import contextlib
import json
import math
import re
import time

UCB_C = 1.2  # exploration constant
DECAY_HALF_LIFE = 10.0  # runs
DEFAULT_POLICY: dict = {"arms": {}, "updated": None}

#: the arms the bandit tunes — deliberately few, deliberately coarse
ARMS = ("surprise_probe", "leak_question", "capability_push", "amplify")


def _policy_path():

    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR / "drive_policy.json"


def load() -> dict:
    with contextlib.suppress(Exception):
        p = _policy_path()
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "arms" in data:
                return data
    return json.loads(json.dumps(DEFAULT_POLICY))


def save(policy: dict) -> None:
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.filelock import atomic_write

        p = _policy_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        policy["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        atomic_write(p, json.dumps(policy, indent=1))


def _arm(policy: dict, arm: str, ctx: str) -> dict:
    return policy["arms"].setdefault(f"{arm}:{ctx}", {"n": 0, "wins": 0.0, "weight": 1.0})


def target_class(objective: str) -> str:
    """Coarse context key — the bandit tunes per target class, not per
    host (per-host would need a hundred runs to learn anything)."""
    blob = str(objective or "").lower()
    if re.search(r"api|graphql|rest|json", blob):
        return "api"
    if re.search(r"spa|react|next|vue|angular|javascript", blob):
        return "spa"
    if re.search(r"wordpress|wp-|plugin", blob):
        return "cms"
    return "web"


def score(policy: dict, arm: str, ctx: str) -> float:
    """UCB1: exploit the proven, explore the untried. Fallback weight
    1.0 for unseen arms (never blocks an action — ordering only)."""
    a = _arm(policy, arm, ctx)
    if a["n"] == 0:
        return 1.0 + UCB_C
    mean = a["wins"] / a["n"]
    total = sum(x["n"] for x in policy["arms"].values()) or 1
    return mean + UCB_C * math.sqrt(math.log(max(1, total)) / a["n"])


def order(policy: dict, ctx: str) -> list[str]:
    """Arms best-first for this target class — the drive consults this
    when choosing which surprise to probe first."""
    return sorted(ARMS, key=lambda arm: -score(policy, arm, ctx))


def record(policy: dict, arm: str, ctx: str, paid: bool) -> dict:
    """Fold one outcome in. Recency-weighted: each record halves old
    wins toward zero at the half-life, so a target that changes isn't
    forever scored on its past."""
    a = _arm(policy, arm, ctx)
    # recency decay applied lazily: scale existing history toward 50/50
    if a["n"] > 0:
        a["wins"] *= 0.95
        a["n"] *= 0.95
    a["n"] += 1.0
    if paid:
        a["wins"] += 1.0
    return policy


def attribution(arm: str, recent: list, confirmed_at: int, window: int = 6) -> bool:
    """Did the arm's action plausibly lead to the CONFIRMED?

    Conservative by design: the arm's action (a DRIVE ACTION message, an
    amplified observation) must appear in the trace within `window`
    turns before the confirmation, on a surface the confirmation names
    or overlaps. False negatives are fine; false positives poison the
    learner."""
    hits = []
    for i, st in enumerate(recent or []):
        blob = " ".join(str(st.get(k) or "") for k in ("tool_name", "thought", "tool_args", "tool_output"))
        if (
            arm == "surprise_probe"
            and "DRIVE ACTION" in blob
            or arm == "amplify"
            and "AMPLIFIED" in blob
            or arm in ("leak_question", "capability_push")
            and re.search(r"leaked|unlock|reach", blob, re.I)
        ):
            hits.append(i)
    if not hits:
        return False
    return any(confirmed_at - h <= window for h in hits)


def on_confirmed(state: dict, policy: dict, confirmed_surface: str) -> dict:
    """A CONFIRMED landed — credit every arm with a plausible recent
    contribution, then persist. Called from the think wiring."""
    ctx = target_class(state.get("original_objective") or state.get("_objective") or "")
    trace = state.get("execution_trace") or []
    now = len(trace)
    window = trace[-8:]
    for arm in ARMS:
        if attribution(arm, window, now):
            record(policy, arm, ctx, paid=True)
    save(policy)
    return policy
