"""Confidence scoring for agent decisions — prevents chasing false positives."""

from __future__ import annotations


def score_confidence(finding_type: str, evidence_quality: str, diff_verified: bool, reproduction_count: int) -> dict:
    base = {
        "sqli": 0.7,
        "xss": 0.6,
        "ssti": 0.5,
        "rce": 0.9,
        "ssrf": 0.7,
        "idor": 0.8,
        "open_redirect": 0.85,
        "info_disclosure": 0.9,
    }
    score = base.get(finding_type, 0.5)
    if diff_verified:
        score += 0.2
    if reproduction_count >= 2:
        score += 0.1
    quality_bonus = {"strong": 0.1, "medium": 0.0, "weak": -0.2}
    score += quality_bonus.get(evidence_quality, 0)
    score = max(0.1, min(1.0, score))
    level = "high" if score >= 0.8 else "medium" if score >= 0.5 else "low"
    return {"score": round(score, 2), "level": level, "should_escalate": score < 0.4, "should_report": score >= 0.6}
