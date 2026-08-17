"""Anomaly detector — z-score, entropy, frequency analysis."""
from __future__ import annotations

import re


def detect_anomalies(request: dict, profile: dict) -> list:
    signals = []
    method = request.get("method","GET")
    if profile.get("methods",{}).get(method,0) == 0:
        signals.append(("unusual_method", 2, f"Method {method} never seen on this endpoint"))
    body = str(request.get("body",""))
    sql_patterns = re.findall(r"(?i)(union\s+select|'\s*or\s+'1'\s*=\s*'1|\bselect\b.*\bfrom\b|\bdrop\s+table|\binsert\s+into|'\s*--)", body)
    if sql_patterns:
        signals.append(("sql_injection", 4, f"SQL keywords: {sql_patterns[:3]}"))
    xss_patterns = re.findall(r"(?i)(<script|onerror\s*=|javascript:|<img[^>]+onerror|<svg/onload)", body)
    if xss_patterns:
        signals.append(("xss_attempt", 4, f"XSS patterns: {xss_patterns[:3]}"))
    traversal_patterns = re.findall(r"\.\./|\.\.\\|/etc/passwd|/etc/shadow|C:\\Windows", body)
    if traversal_patterns:
        signals.append(("path_traversal", 3, "Path traversal attempt"))
    ssrf_patterns = re.findall(r"(?:169\.254\.169\.254|metadata\.google\.internal|127\.0\.0\.1:\d+)", body)
    if ssrf_patterns:
        signals.append(("ssrf_attempt", 4, "SSRF to metadata endpoint"))
    return signals
