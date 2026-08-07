"""Attribution engine — attempt to attribute attacks."""
import json
from pathlib import Path

SIGNATURES = {"sqlmap": ["sqlmap/1.","sqlmap#"], "nmap": ["nmap scripting engine"], "nikto": ["nikto/"], "burp": ["burpsuite"]}

def attribute_attack(request: dict) -> dict:
    ua = request.get("user_agent","")
    for tool, sigs in SIGNATURES.items():
        for sig in sigs:
            if sig.lower() in ua.lower():
                return {"tool": tool, "confidence": "high"}
    body = str(request.get("body",""))
    if "sqlmap" in body.lower(): return {"tool": "sqlmap", "confidence": "medium"}
    return {"tool": "unknown", "confidence": "low"}
