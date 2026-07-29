"""Burp Suite XML export — generate Burp-compatible finding reports."""
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

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
    path = output_path or f"medusa_agent/reports/burp_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.xml"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path
