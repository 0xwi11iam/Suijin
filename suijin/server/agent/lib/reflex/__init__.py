"""The reflex layer — System One decisions for the red path.

The problem this package exists to kill (operator analysis, 2026-10-02):
the supervisor, oracle, and drift detection burned GENERATION on
JUDGMENT — an LLM asked "is the agent drifting?" will always have an
opinion (RLHF-trained helpfulness), so healthy traces got invented
problems, confident prose at 100% certainty, and no way to measure
whether any nudge helped. "Mostly bullshit" — the operator's words.

The fix is architectural, per concept:

  supervisor/  — the intervention pipeline: tier-0 rules (kept: free,
                 deterministic), tier-1 classifier verdict with ABSTAIN
                 as a first-class outcome, tier-2 phrasing (the ONLY LLM
                 left, grounded in the confirmed decision, OFFER-shaped)
  drifter/     — per-turn objective alignment promoted to first class:
                 a drift TIMELINE, not a snapshot; trajectory
                 confirmation kills single-turn false positives
  oracle/      — triage (anomaly CLASS on tool output, ms-priced) and
                 adjudication (confirm/deny/need-more per hypothesis);
                 generation stays LLM — judgment does not
  core/        — the spine: typed-question client (transport-agnostic:
                 laya-mlx adapter + a deterministic abstain-first local
                 classifier so the pipeline works before laya lands),
                 the question registry (the labeling contract), feature
                 builders, the fallback ladder, and shadow mode (the
                 outcome-labeled corpus that trains laya and measures
                 intervention precision — the number that proves the
                 supervisor stopped bullshitting)

Everything is behind `decision` config; absent/disabled = exactly
today's behavior. Silence is the DEFAULT outcome on healthy traces —
that is the whole point.
"""

from suijin.modules.agent.lib.reflex.core.client import DecisionClient, decide
from suijin.modules.agent.lib.reflex.core.questions import QSET_VERSION, Question, questions
from suijin.modules.agent.lib.reflex.core.shadow import shadow_log

__all__ = ["DecisionClient", "decide", "QSET_VERSION", "Question", "questions", "shadow_log"]
