"""Finding verifier (A1) + peer review (A2) — no unproven claim reaches the report.

verify_findings: each finding with confidence 'probable'/'suspected' and a
re-verifiable shape gets an INDEPENDENT second check (a different evidence
path than the one that produced it — different tool class where possible).
Outcomes: verified (second evidence path agrees) / downgraded (claim kept
at 'suspected') / dismissed (contradicted).

peer_review: two adversarial LLM passes over the finding list — a skeptic
attacks each claim, a judge weighs the attack. Never fatal; findings
without LLM access pass through with a note.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("suijin.verify")

# finding-type -> independent verification recipe (SECOND evidence path)
_RECIPES: dict[str, dict] = {
    "sqli": {
        "tool": "http_request",
        "expect_absent": ("sql syntax", "mysql_fetch", "ORA-"),
        "note": "error-based echo",
    },
    "xss": {"tool": "extract_artifacts", "marker": "<script>", "note": "reflection capture"},
    "open_redirect": {"tool": "open_redirect_check", "note": "30x Location canary"},
    "cors": {"tool": "cors_check", "note": "origin reflection + credentials"},
    "secret": {"tool": "scan_secrets", "note": "pattern + entropy re-scan"},
    # H5: five more classes (the audit found only 5 recipes — everything
    # else fell to the weak keyword fallback)
    "ssrf": {"tool": "ssrf_canary", "note": "OOB callback observed"},
    "cmd_injection": {"tool": "http_request", "marker": "uid=", "note": "command output echo"},
    "command_injection": {"tool": "http_request", "marker": "uid=", "note": "command output echo"},
    "jwt": {"tool": "jwt_inspect", "note": "algorithm/claim re-read"},
    "idor": {"tool": "http_request", "note": "cross-user object access"},
    "info_disclosure": {"tool": "http_request", "note": "sensitive data re-read"},
}
# synonyms -> canonical recipe key
_RECIPE_ALIASES = {
    "sql_injection": "sqli",
    "injection": "sqli",
    "command_injection": "cmd_injection",
    "rce": "cmd_injection",
    "ssrf_vulnerability": "ssrf",
    "bola": "idor",
    "jwt_vulnerability": "jwt",
    "leak": "info_disclosure",
    "disclosure": "info_disclosure",
}


def verify_finding(finding: dict, route_fn=None) -> dict:
    """Independent second check for ONE finding. Returns the finding with
    verification added: {verdict: verified|downgraded|dismissed|unverifiable,
    evidence: str}."""
    if route_fn is None:
        from suijin.modules.tools.lib.dispatch import route_tool

        route_fn = route_tool
    ftype = str(finding.get("type", "")).lower()
    evidence = str(finding.get("evidence", ""))
    out = dict(finding)
    key = _RECIPE_ALIASES.get(ftype, ftype)
    recipe = _RECIPES.get(key)
    if recipe is None:
        out["verification"] = {"verdict": "unverifiable", "evidence": "no independent recipe for this finding type"}
        return out
    target = finding.get("target") or finding.get("url") or finding.get("endpoint") or ""
    if not target:
        out["verification"] = {"verdict": "unverifiable", "evidence": "finding carries no re-checkable target"}
        return out
    try:
        second = str(route_fn(recipe["tool"], {"url": target, "text": evidence, "host": target}, {}) or "")
    except Exception as e:  # noqa: BLE001 — verification failures are data
        out["verification"] = {"verdict": "unverifiable", "evidence": f"second pass errored: {e}"}
        return out
    if second.startswith("Error"):
        out["verification"] = {"verdict": "unverifiable", "evidence": f"second pass tool error: {second[:120]}"}
        return out
    # echo/response markers: does the independent path SHOW the same problem?
    # the strongest signal is the ORIGINAL ARTIFACT reappearing in the
    # independent output (same key, same redirect, same reflection)
    artifact = evidence.strip()
    marker = recipe.get("marker") or (artifact if 8 <= len(artifact) <= 200 else None)
    if marker and marker.lower() in second.lower():
        out["verification"] = {
            "verdict": "verified",
            "evidence": f"independent {recipe['tool']} shows the marker ({recipe['note']})",
        }
        return out
    absent = recipe.get("expect_absent")
    if absent and not any(a.lower() in second.lower() for a in absent):
        out["verification"] = {
            "verdict": "dismissed",
            "evidence": f"independent {recipe['tool']} CONTRADICTS: expected indicators absent in the second pass",
        }
        return out
    # H5: the old keyword-mention fallback counted 'output mentions sqli'
    # as VERIFIED — that manufactured confidence. Neither confirms nor
    # contradicts is now honestly 'downgraded'.
    out["verification"] = {
        "verdict": "downgraded",
        "evidence": f"independent {recipe['tool']} neither confirms nor contradicts ({second[:100]})",
    }
    return out


def verify_findings(findings: list, route_fn=None, only_unverified: bool = True) -> list:
    """Verify a findings list; each gets its verification dict attached."""
    out = []
    for f in findings or []:
        if only_unverified and str(f.get("confidence", "probable")) == "verified":
            out.append(f)
            continue
        out.append(verify_finding(f, route_fn))
    return out


_SKEPTIC_PROMPT = """You are a hostile reviewer of security findings. Attack each claim:
what evidence is missing, what else could explain it, what would a defender say?
Findings: {findings}
Respond as JSON: {{"attacks": [{{"id": 1, "attack": "...", "severity": "fatal|weak|none"}}]}}
JSON only."""

_JUDGE_PROMPT = """A skeptic attacked these security findings. Weigh each attack against the original evidence.
Findings: {findings}
Attacks: {attacks}
Respond as JSON: {{"verdicts": [{{"id": 1, "verdict": "keep|downgrade|dismiss", "reason": "..."}}]}}
JSON only."""


async def _llm_json(prompt: str, generate_fn, config) -> dict | None:
    try:
        raw = await generate_fn([{"role": "user", "content": prompt}], config or {})
    except Exception as e:  # noqa: BLE001
        logger.debug("peer-review LLM failed: %s", e)
        return None
    if not isinstance(raw, str) or raw.startswith("Error"):
        return None
    try:
        start, end = raw.find("{"), raw.rfind("}")
        return json.loads(raw[start : end + 1])
    except (ValueError, TypeError):
        return None


def peer_review(findings: list, config: dict | None = None, generate_fn=None) -> dict:
    """A2: skeptic + judge LLM passes. Returns {reviewed: [...], source}.

    Each finding gains 'peer_review': {verdict, reason}. Falls back to
    marking everything 'keep' with source='no-llm' when unavailable."""
    if not findings:
        return {"reviewed": [], "source": "none"}
    if generate_fn is None:
        from suijin.modules.redteam.lib.red.llm_client import generate_async as generate_fn
    import asyncio

    slim = [
        {
            "id": i + 1,
            "type": f.get("type"),
            "evidence": str(f.get("evidence", ""))[:200],
            "confidence": f.get("confidence", "probable"),
        }
        for i, f in enumerate(findings)
    ]
    attacks = asyncio.run(_llm_json(_SKEPTIC_PROMPT.format(findings=json.dumps(slim)), generate_fn, config))
    if attacks is None:
        return {
            "reviewed": [
                dict(f, peer_review={"verdict": "keep", "reason": "no LLM available for review"}) for f in findings
            ],
            "source": "no-llm",
        }
    verdicts = asyncio.run(
        _llm_json(
            _JUDGE_PROMPT.format(findings=json.dumps(slim), attacks=json.dumps(attacks.get("attacks", []))),
            generate_fn,
            config,
        )
    )
    vmap = {int(v.get("id", 0)): v for v in (verdicts or {}).get("verdicts", [])} if verdicts else {}
    reviewed = []
    for i, f in enumerate(findings, 1):
        v = vmap.get(i, {})
        reviewed.append(
            dict(f, peer_review={"verdict": v.get("verdict", "keep"), "reason": v.get("reason", "unreviewed")})
        )
    return {"reviewed": reviewed, "source": "llm"}


def render_review(findings: list) -> str:
    lines = []
    for f in findings:
        v = f.get("verification") or {}
        pr = f.get("peer_review") or {}
        tag = v.get("verdict", "-")
        pr_tag = pr.get("verdict", "-")
        lines.append(
            f"  [{f.get('type', '?'):14}] conf={f.get('confidence', '?'):9} verify={tag:13} peer={pr_tag:9} {f.get('evidence', '')[:60]}"
        )
    return "\n".join(lines)
