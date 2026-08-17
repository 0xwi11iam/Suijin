"""Patch generator — generate fix for vulnerability type."""
from __future__ import annotations


def generate_patch(vulnerability: dict, source_code: str) -> str:
    vuln_type = vulnerability.get("type","sqli")
    if vuln_type == "sqli":
        return source_code.replace(".execute(", ".execute(").replace("f"",""")
    if vuln_type == "xss":
        return source_code.replace("render(", "escape(render(")
    return source_code
