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


# v7.2: register the new binary questions
for _q in [
    Question(
        qid="hypothesis.worth_testing",
        choices=("yes", "no", "none"),
        features=("hypothesis_text", "target_value", "iteration_budget", "phase"),
        threshold=0.55,
        confirm_next_turn=True,
        note="Binary: is this hypothesis worth spending iterations on right now?",
        instructions="Given the current engagement state and the hypothesis text, decide if testing this hypothesis is worth the iteration cost. 'yes' means the potential impact justifies the spend; 'no' means skip it.",
        criteria=(
            (
                "yes",
                "the hypothesis targets a high-value surface, has clear impact, and is feasible to test in the current phase",
            ),
            (
                "no",
                "the hypothesis is speculative, targets a low-value surface, or would take too many iterations for the expected impact",
            ),
        ),
    ),
    Question(
        qid="chain.promising",
        choices=("yes", "no", "none"),
        features=("chain_source", "chain_target", "capabilities_held", "confirmed_count"),
        threshold=0.55,
        confirm_next_turn=True,
        note="Binary: does this chain have real escalation potential?",
        instructions="Given a confirmed finding and a proposed chain step, decide if the chain leads to meaningful escalation. 'yes' means the chain could reach data access, privilege escalation, or RCE; 'no' means it's a dead end.",
        criteria=(
            (
                "yes",
                "the chain step uses the finding to reach a new surface, gain access to data, or escalate privileges",
            ),
            ("no", "the chain step doesn't meaningfully advance impact or reaches a dead end"),
        ),
    ),
    Question(
        qid="surface.high_value",
        choices=("yes", "no", "none"),
        features=("surface_url", "auth_requirement", "data_sensitivity", "method"),
        threshold=0.55,
        confirm_next_turn=True,
        note="Binary: is this endpoint worth going deep on?",
        instructions="Given an endpoint's characteristics, decide if it's worth focused deep testing vs. moving on. 'yes' means the endpoint handles sensitive data, has complex logic, or is admin-facing; 'no' means it's a simple public endpoint.",
        criteria=(
            ("yes", "the endpoint handles payments, user data, admin functions, file operations, or authentication"),
            ("no", "the endpoint serves static content, is a health check, or has no security-relevant logic"),
        ),
    ),
    Question(
        qid="finding.real",
        choices=("real", "false_positive", "none"),
        features=("finding_type", "evidence", "control_difference", "marker_match"),
        threshold=0.60,
        confirm_next_turn=True,
        note="Binary: is this finding a real vulnerability or a false positive?",
        instructions="Given the evidence for a potential finding, decide if it's real. 'real' means the evidence supports a genuine vulnerability; 'false_positive' means the observation has an innocent explanation.",
        criteria=(
            ("real", "the evidence shows a clear security impact that wouldn't occur in a properly configured system"),
            (
                "false_positive",
                "the observation is expected behavior, a scanner artifact, or has an innocent explanation",
            ),
        ),
    ),
]:
    _QUESTIONS[_q.qid] = _q


def questions() -> dict[str, Question]:
    return dict(_QUESTIONS)

    (
        Question(
            qid="hypothesis.worth_testing",
            choices=("yes", "no", "none"),
            features=("hypothesis_text", "target_value", "iteration_budget", "phase"),
            threshold=0.55,
            confirm_next_turn=True,
            note="Binary: is this hypothesis worth spending iterations on right now?",
            instructions="Given the current engagement state and the hypothesis text, decide if testing this hypothesis is worth the iteration cost. 'yes' means the potential impact justifies the spend; 'no' means skip it.",
            criteria=(
                (
                    "yes",
                    "the hypothesis targets a high-value surface, has clear impact, and is feasible to test in the current phase",
                ),
                (
                    "no",
                    "the hypothesis is speculative, targets a low-value surface, or would take too many iterations for the expected impact",
                ),
            ),
        ),
    )
    (
        Question(
            qid="chain.promising",
            choices=("yes", "no", "none"),
            features=("chain_source", "chain_target", "capabilities_held", "confirmed_count"),
            threshold=0.55,
            confirm_next_turn=True,
            note="Binary: does this chain have real escalation potential?",
            instructions="Given a confirmed finding and a proposed chain step, decide if the chain leads to meaningful escalation. 'yes' means the chain could reach data access, privilege escalation, or RCE; 'no' means it's a dead end.",
            criteria=(
                (
                    "yes",
                    "the chain step uses the finding to reach a new surface, gain access to data, or escalate privileges",
                ),
                ("no", "the chain step doesn't meaningfully advance impact or reaches a dead end"),
            ),
        ),
    )
    (
        Question(
            qid="surface.high_value",
            choices=("yes", "no", "none"),
            features=("surface_url", "auth_requirement", "data_sensitivity", "method"),
            threshold=0.55,
            confirm_next_turn=True,
            note="Binary: is this endpoint worth going deep on?",
            instructions="Given an endpoint's characteristics, decide if it's worth focused deep testing vs. moving on. 'yes' means the endpoint handles sensitive data, has complex logic, or is admin-facing; 'no' means it's a simple public endpoint.",
            criteria=(
                (
                    "yes",
                    "the endpoint handles payments, user data, admin functions, file operations, or authentication",
                ),
                ("no", "the endpoint serves static content, is a health check, or has no security-relevant logic"),
            ),
        ),
    )
    (
        Question(
            qid="finding.real",
            choices=("real", "false_positive", "none"),
            features=("finding_type", "evidence", "control_difference", "marker_match"),
            threshold=0.60,
            confirm_next_turn=True,
            note="Binary: is this finding a real vulnerability or a false positive?",
            instructions="Given the evidence for a potential finding, decide if it's real. 'real' means the evidence supports a genuine vulnerability; 'false_positive' means the observation has an innocent explanation.",
            criteria=(
                (
                    "real",
                    "the evidence shows a clear security impact that wouldn't occur in a properly configured system",
                ),
                (
                    "false_positive",
                    "the observation is expected behavior, a scanner artifact, or has an innocent explanation",
                ),
            ),
        ),
    )


def get_question(qid: str) -> Question | None:
    return _QUESTIONS.get(qid)
