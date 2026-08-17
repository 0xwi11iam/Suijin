"""Endpoint risk scorer — pre-score inherent risk."""
from __future__ import annotations


def score_endpoint_risk(endpoint: dict) -> dict:
    score = 1
    reasons = []
    if endpoint.get("auth") == "none": score += 3; reasons.append("no_auth")
    if endpoint.get("auth") == "public": score += 1; reasons.append("public")
    if endpoint.get("has_sql", False): score += 2; reasons.append("raw_sql")
    if "admin" in endpoint.get("path","").lower(): score += 2; reasons.append("admin_path")
    if "reset" in endpoint.get("path","").lower() or "password" in endpoint.get("path","").lower():
        score += 3; reasons.append("sensitive_endpoint")
    if not endpoint.get("has_rate_limit", False): score += 1; reasons.append("no_rate_limit")
    level = "critical" if score >= 7 else "high" if score >= 5 else "medium" if score >= 3 else "low"
    return {"risk_score": min(10, score), "level": level, "reasons": reasons}
