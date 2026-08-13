"""Traffic scorer — 1-10 score per request."""
from __future__ import annotations
from medusa.core.blue.traffic.anomaly_detector import detect_anomalies

def score_request(request: dict, profile: dict, attacker_profile=None) -> dict:
    signals = detect_anomalies(request, profile)
    score = 1
    reasons = []
    for name, weight, detail in signals:
        score = min(10, score + weight)
        reasons.append(f"[{name}] {detail}")
    ip = request.get("ip","")
    if ip not in profile.get("ips", set()):
        score = min(10, score + 2)
        reasons.append("[new_ip] IP never seen on this endpoint")
    body_size = len(str(request.get("body","")))
    avg_size = profile.get("avg_body_size", 1000)
    if avg_size > 0 and body_size > avg_size * 3:
        score = min(10, score + 1)
        reasons.append(f"[body_anomaly] Body {body_size} bytes vs avg {avg_size}")
    level = "critical" if score >= 8 else "suspicious" if score >= 5 else "noise"
    action = "block" if score >= 8 else "validate" if score >= 5 else "log"
    return {"score": score, "level": level, "action": action, "reasons": reasons, "signals": [s[0] for s in signals]}
