"""Attack classifier — identify attack type from request."""
from __future__ import annotations

import re


def classify_attack(request: dict) -> dict:
    body = str(request.get("body","")) + " " + str(request.get("url",""))
    if re.search(r"(?i)(union\s+select|'\s*or\s+'1|\bselect\b.*\bfrom\b.*where)", body):
        return {"type": "SQL Injection", "confidence": 0.9, "category": "injection"}
    if re.search(r"(?i)(<script|onerror\s*=|javascript:|<img[^>]+onerror)", body):
        return {"type": "Cross-Site Scripting (XSS)", "confidence": 0.85, "category": "injection"}
    if re.search(r"(?i)(\.\./|\.\.\\|/etc/passwd)", body):
        return {"type": "Path Traversal", "confidence": 0.9, "category": "access"}
    if re.search(r"169\.254\.169\.254|metadata\.google\.internal", body):
        return {"type": "SSRF", "confidence": 0.95, "category": "access"}
    if re.search(r"(?i)(\{\{.*\}\}|\$\{.*\}|<%=.*%>)", body):
        return {"type": "SSTI", "confidence": 0.8, "category": "injection"}
    if re.search(r"(?i)(nmap|sqlmap|nikto|dirbuster|gobuster|burp)", str(request.get("user_agent",""))):
        return {"type": "Automated Scan", "confidence": 0.8, "category": "recon"}
    return {"type": "Unknown Anomaly", "confidence": 0.3, "category": "unknown"}
