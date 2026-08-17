"""Attacker fingerprint — browser, TLS, TCP stack."""
from __future__ import annotations


def fingerprint_request(request: dict) -> dict:
    ua = request.get("user_agent","")
    fp = {"browser": "unknown", "os": "unknown", "tools": []}
    if "sqlmap" in ua.lower(): fp["tools"].append("sqlmap")
    if "nmap" in ua.lower(): fp["tools"].append("nmap")
    if "python" in ua.lower(): fp["tools"].append("python_script")
    if "burp" in ua.lower(): fp["tools"].append("burp_suite")
    if "curl" in ua.lower(): fp["tools"].append("curl")
    if "chrome" in ua.lower(): fp["browser"] = "chrome"
    elif "firefox" in ua.lower(): fp["browser"] = "firefox"
    if "windows" in ua.lower(): fp["os"] = "windows"
    elif "mac" in ua.lower(): fp["os"] = "macos"
    elif "linux" in ua.lower(): fp["os"] = "linux"
    return fp
