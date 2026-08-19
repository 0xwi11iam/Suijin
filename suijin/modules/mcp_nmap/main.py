"""MCP Nmap — structured XML output parsing."""

import subprocess, shlex, json, xml.etree.ElementTree as ET, tempfile, os


def mcp_nmap_scan(target, flags="-sV -sC -T4"):
    if not target:
        return "Error: target required"
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
        xml_path = f.name
    try:
        cmd = f"nmap {flags} -oX {shlex.quote(xml_path)} {shlex.quote(target)}"
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300, cwd="/tmp")
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            hosts = []
            for host in root.findall("host"):
                addr = host.find("address").get("addr", "?")
                ports = []
                for port in host.findall(".//port"):
                    svc = port.find("service")
                    ports.append(
                        {
                            "port": port.get("portid"),
                            "protocol": port.get("protocol"),
                            "state": port.find("state").get("state"),
                            "service": svc.get("name") if svc is not None else "?",
                            "product": (svc.get("product", "") if svc is not None else ""),
                            "version": (svc.get("version", "") if svc is not None else ""),
                        }
                    )
                hosts.append({"ip": addr, "ports": ports})
            return json.dumps(hosts, indent=2)
        except Exception:
            return r.stdout or r.stderr or "(no output)"
    finally:
        os.unlink(xml_path)
