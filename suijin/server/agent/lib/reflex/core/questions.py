"""The question registry — the labeling contract between Suijin and any
System One decision model (laya-mlx).

A question is a CLOSED choice set over a NAMED feature digest. This file
is the spec: changing a choice or a feature renames reality for a
trained classifier, so the qset version gates compatibility (an old
model rejects cleanly instead of misfiring on new semantics).

Design rules, from the operator's diagnosis:
- ABSTAIN IS ALWAYS A CHOICE, and on every question the abstain label
  is the one a healthy engagement should dominate. A classifier that
  cannot say "nothing wrong" gets erased by this registry.
- Choices are FEW and FALSIFIABLE — each maps to a defined intervention
  or a defined silence. No prose in, no prose out.
"""

from __future__ import annotations

from dataclasses import dataclass

#: bump on ANY semantic change to choices/features; the client refuses
#: a model whose qset doesn't match (clean disable, never a misfire)
QSET_VERSION = 1

ABSTAIN = "none"


@dataclass(frozen=True)
class Question:
    qid: str  # stable id, e.g. "supervisor.verdict"
    choices: tuple[str, ...]
    features: tuple[str, ...]  # exact feature names the digest must carry
    threshold: float = 0.55  # min p() for a non-abstain choice to act
    confirm_next_turn: bool = True  # borderline → timeline mark, act on repeat
    note: str = ""
    #: the System One phrasings — instructions reference state FIELDS and
    #: criteria carry per-label DEFINITIONS (laya's trained format). These
    #: are the fine-tuning contract too: the labels below are the classes.
    instructions: str = ""
    criteria: tuple[tuple[str, str], ...] = ()

    def laya(self) -> dict:
        """The question in laya-mlx's trained dict format."""
        crit = dict(self.criteria) or {c: c for c in self.choices}
        return {
            "type": "choice",
            "instructions": self.instructions or f"Answer from state fields: {', '.join(self.features)}.",
            "criteria": crit,
        }


_QUESTIONS: dict[str, Question] = {}


def _q(q: Question) -> Question:
    _QUESTIONS[q.qid] = q
    return q


# ── supervisor ───────────────────────────────────────────────────────────
_q(
    Question(
        qid="supervisor.verdict",
        choices=(ABSTAIN, "drift", "stall", "repeat", "miss_chain"),
        features=(
            "phase",
            "iteration",
            "iterations_since_finding",
            "repeat_pressure",  # 0..1 — repeated identical tool+args share
            "phase_tool_mismatch",  # recon-tools-during-exploitation count
            "tool_fail_cluster",  # consecutive failures, last 5
            "chain_candidates",  # unexploited confirmed findings in state
            "cost_rate_usd",  # spend per iteration, trailing
            "drift_score",  # drifter alignment output, 0..1
            "unheeded_count",  # interventions ignored so far
        ),
        threshold=0.62,
        note="the per-interval intervention verdict; abstain dominates on healthy traces",
        instructions=(
            "A security engagement agent runs think-act cycles. From `phase`, "
            "`iterations_since_finding`, `repeat_pressure`, `tool_fail_cluster`, "
            "`phase_tool_mismatch`, `chain_candidates` and `drift_score` in the state: "
            "is the agent degraded?"
        ),
        criteria=(
            ("none", "healthy progress: recent steps advance the objective"),
            ("drift", "actions no longer serve the objective: high drift_score, recon-shaped tools in late phases"),
            ("stall", "no forward motion: many iterations since the last finding, or clustered failures"),
            ("repeat", "the same tool call with near-identical arguments repeats: high repeat_pressure"),
            (
                "miss_chain",
                "a confirmed finding sits unexploited: chain_candidates above zero while work continues elsewhere",
            ),
        ),
    )
)

_q(
    Question(
        qid="supervisor.fire_coach",
        choices=(ABSTAIN, "fire"),
        features=(
            "speakworthy_facts",  # count from facts_brief
            "last_intervention_turns_ago",
            "drift_score",
            "heeded_last",
        ),
        threshold=0.5,
        confirm_next_turn=False,  # the coach's own structural gate already ran
        note="whether an LLM phrasing call is worth it AT ALL this turn",
    )
)

# ── drifter ──────────────────────────────────────────────────────────────
_q(
    Question(
        qid="drifter.alignment",
        choices=(ABSTAIN, "on_course", "drifting", "off_course"),
        features=(
            "objective_digest",  # hashed normalized objective keywords
            "recent_actions_digest",  # tool-class mix of last 5 turns
            "phase",
            "phase_tool_mismatch",
            "iterations_since_finding",
        ),
        threshold=0.6,
        note="per-turn (10ms-class); abstain = not enough signal yet",
        instructions=(
            "An autonomous security agent works toward an objective. From `phase`, "
            "`phase_tool_mismatch`, `iterations_since_finding`, `objective_digest` and "
            "`recent_actions_digest`: is the recent action mix aligned with the objective?"
        ),
        criteria=(
            ("none", "not enough signal yet to judge alignment"),
            ("on_course", "the action mix matches the current phase and objective"),
            ("drifting", "actions are only loosely connected to the objective"),
            ("off_course", "actions clearly serve something other than the objective"),
        ),
    )
)

# ── oracle ───────────────────────────────────────────────────────────────
_q(
    Question(
        qid="oracle.triage",
        choices=(ABSTAIN, "anomaly_auth", "anomaly_inject", "anomaly_info", "anomaly_logic"),
        features=(
            "status_code",
            "body_len_delta",  # vs the engagement's rolling median
            "elapsed_ms",
            "error_markers",  # count of stack/error tokens
            "reflect_indicators",  # reflected-input evidence count
        ),
        threshold=0.55,
        confirm_next_turn=False,  # triage gates the LLM, doesn't inject text
        note="ms-priced per-request anomaly CLASS; replaces regex-only triage as tier-1",
        instructions=(
            "An HTTP response from a security test. From `status_code`, `body_len_delta`, "
            "`elapsed_ms`, `error_markers` and `reflect_indicators`: does the response "
            "look anomalous in a security-relevant way?"
        ),
        criteria=(
            ("none", "an ordinary response for this endpoint"),
            ("anomaly_auth", "authentication or authorization behaves unexpectedly: unusual 401/403/redirect"),
            ("anomaly_inject", "injection evidence: reflected input, SQL or template markers in the body"),
            ("anomaly_info", "information disclosure: errors, stack traces, internal details"),
            ("anomaly_logic", "a business-logic anomaly: unexpected success, wrong data, odd timing"),
        ),
    )
)

_q(
    Question(
        qid="oracle.adjudicate",
        choices=(ABSTAIN, "confirm", "deny", "need_more"),
        features=(
            "hypothesis_class",
            "evidence_markers",
            "control_difference",  # payload-vs-control response delta, 0..1
            "claimed_confidence",
        ),
        threshold=0.6,
        note="per-hypothesis verdict; deny feeds the cheatsheet as a dead battery",
    )
)

# ── drive ────────────────────────────────────────────────────────────────
_q(
    Question(
        qid="drive.probe_worth",
        choices=(ABSTAIN, "probe", "skip"),
        features=(
            "surprise",  # epistemic surprise of last observation
            "last_probe_turns_ago",
            "phase",
            "open_questions",
        ),
        threshold=0.5,
        note="reflex probes at per-turn cadence once this is live",
    )
)


def questions() -> dict[str, Question]:
    return dict(_QUESTIONS)


def get_question(qid: str) -> Question | None:
    return _QUESTIONS.get(qid)
