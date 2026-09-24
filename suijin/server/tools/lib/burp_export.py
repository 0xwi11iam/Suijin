"""Burp Suite XML export — generate Burp-compatible finding reports."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


def _ws_dir():
    """Workspace dir (honours a monkeypatched module attr)."""
    v = globals().get("WORKSPACE_DIR")
    if v is not None:
        return v
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR


def __getattr__(name):
    if name == "WORKSPACE_DIR":
        return _ws_dir()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def export_burp_xml(findings: list, output_path: str = None) -> str:
    root = ET.Element("issues")
    for i, f in enumerate(findings, 1):
        issue = ET.SubElement(root, "issue")
        ET.SubElement(issue, "serialNumber").text = str(i)
        ET.SubElement(issue, "type").text = str(f.get("finding_type", "Unknown"))[:10]
        ET.SubElement(issue, "name").text = str(f.get("description", f.get("type", "Finding")))[:200]
        ET.SubElement(issue, "severity").text = str(f.get("severity", "Info")).capitalize()
        ET.SubElement(issue, "confidence").text = str(f.get("confidence", "Certain"))
        ET.SubElement(issue, "host").text = str(f.get("host", ""))
        ET.SubElement(issue, "path").text = str(f.get("path", "/"))
        ET.SubElement(issue, "issueDetail").text = str(f.get("detail", f.get("evidence", "")))[:2000]
        ET.SubElement(issue, "remediationBackground").text = str(f.get("remediation", ""))[:1000]
    tree = ET.ElementTree(root)
    path = output_path or str(
        _ws_dir() / "reports" / f"burp_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.xml"
    )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path
