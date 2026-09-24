"""Objective decomposer (A5) — dependency-ordered subtask planning.

One LLM pass turns an objective into a checklist the agent (or operator)
can execute in order. Degrades to a heuristic split when no LLM is
available. Output: ordered subtasks with depends_on + why.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("suijin.decompose")

_PROMPT = """Break this security engagement objective into 3-8 ordered subtasks.

Objective: {objective}

Respond as JSON: {{"subtasks": [
  {{"id": 1, "task": "...", "depends_on": [], "why": "one line"}}
]}}
Depends_on lists earlier ids. Keep tasks concrete (scan X, test Y, verify Z).
JSON only."""


def decompose(objective: str, config: dict | None = None, generate_fn=None) -> dict:
    """Returns {subtasks: [...], source: 'llm'|'heuristic'}.

    Never raises: LLM/parse failures fall back to the heuristic split.
    """
    if not objective.strip():
        return {"subtasks": [], "source": "empty"}
    plan = None
    source = "heuristic"
    if generate_fn is not None:
        try:
            import asyncio

            raw = asyncio.run(
                generate_fn([{"role": "user", "content": _PROMPT.format(objective=objective[:400])}], config or {})
            )
            if isinstance(raw, str) and not raw.startswith("Error"):
                start, end = raw.find("{"), raw.rfind("}")
                data = json.loads(raw[start : end + 1])
                subs = data.get("subtasks")
                if isinstance(subs, list) and subs:
                    plan = subs
                    source = "llm"
        except Exception as e:  # noqa: BLE001 — planning must never raise
            logger.debug("LLM decompose failed: %s", e)
    if plan is None:
        plan = _heuristic(objective)
        source = "heuristic"
    return {"subtasks": plan, "source": source}


def _heuristic(objective: str) -> list[dict]:
    """Offline fallback: the universal recon->test->verify->report spine."""
    low = objective.lower()
    subs = [
        {
            "id": 1,
            "task": f"Recon and fingerprint the target(s) in scope for: {objective[:80]}",
            "depends_on": [],
            "why": "Every engagement starts with an accurate attack surface.",
        }
    ]
    subs.append(
        {
            "id": 2,
            "task": "Enumerate exposed services, endpoints, and versions",
            "depends_on": [1],
            "why": "Versioned surface drives vuln matching.",
        }
    )
    if any(w in low for w in ("web", "http", "api", "site", "app")):
        subs.append(
            {
                "id": 3,
                "task": "Web testing: headers, auth, injection points, business logic",
                "depends_on": [2],
                "why": "Objective mentions a web surface.",
            }
        )
    else:
        subs.append(
            {
                "id": 3,
                "task": "Service-specific testing against exposed versions",
                "depends_on": [2],
                "why": "Matched CVEs and misconfigs first.",
            }
        )
    subs.append(
        {
            "id": 4,
            "task": "Verify every candidate finding with independent evidence",
            "depends_on": [3],
            "why": "No unverified claims in the report.",
        }
    )
    subs.append(
        {
            "id": 5,
            "task": "Write the report with severity, evidence, and remediation",
            "depends_on": [4],
            "why": "Deliverable.",
        }
    )
    return subs


def render(plan: dict) -> str:
    lines = []
    for st in plan.get("subtasks") or []:
        deps = f" (after {','.join(map(str, st.get('depends_on') or []))})" if st.get("depends_on") else ""
        lines.append(f"  [{st.get('id', '?')}] {st.get('task', '?')}{deps}")
        if st.get("why"):
            lines.append(f"        — {st['why']}")
    head = f"plan ({plan.get('source', '?')}):" if lines else "no subtasks"
    return head + "\n" + "\n".join(lines)
