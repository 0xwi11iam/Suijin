"""Recon orchestration: chain discovery and version-to-CVE lookups.

`recon_chain` runs nmap, parses open services with version banners, and pulls
matching CVEs so a single call replaces the manual "scan, fingerprint, lookup"
hop-by-hop loop.
"""

from __future__ import annotations

import re

# nmap -sV service table lines: "PORT/STATE SERVICE VERSION"
_SERVICE_RE = re.compile(r"^(\d+)/(tcp|udp)\s+open\s+(\S+)\s*(.*)$", re.MULTILINE)
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+[a-zA-Z0-9._-]*)")


def parse_services(nmap_output: str) -> list[dict]:
    """Extract {port, proto, service, banner} from nmap -sV output."""
    services = []
    for m in _SERVICE_RE.finditer(nmap_output or ""):
        port, proto, service, rest = m.groups()
        services.append(
            {
                "port": int(port),
                "proto": proto,
                "service": service,
                "banner": (rest or "").strip()[:160],
            }
        )
    return services


def version_to_cves(services: list[dict], config) -> list[tuple]:
    """Return (port, product, version, cve_text) for services with a version."""
    from medusa.tools.intel import search_cve

    results = []
    for s in services:
        banner = s.get("banner", "")
        product = s.get("service", "")
        version = None
        m = _VERSION_RE.search(banner)
        if m:
            version = m.group(1)
            pre = banner[: m.start()].strip()
            if pre:
                product = pre.split()[-1]
            cves = search_cve(product, config, version=version, limit=3)
            results.append((s["port"], product, version, cves))
    return results


def recon_chain(target: str, config=None, ports: str | None = None) -> str:
    """nmap -> service fingerprint -> CVE lookup, returned as one report."""
    if not target:
        return "Error: target required"

    from medusa.modules.loader import get_module_tools

    nmap_scan = get_module_tools().get("nmap_scan")
    if nmap_scan is None:
        return "Error: nmap_scan module tool is not loaded."

    flags = "-sV -sC -T4" + (f" -p {ports}" if ports else "")
    nmap_out = nmap_scan(target, flags=flags)

    services = parse_services(nmap_out)
    lines = [f"# Recon chain: {target}\n", nmap_out]

    if not services:
        lines.append("\n(no open services parsed from nmap output)")
        return "\n".join(lines)

    lines.append("\n## Services discovered")
    for s in services:
        lines.append(f"- {s['port']}/{s['proto']} {s['service']} {s['banner']}")

    lines.append("\n## CVE matches (version-based)")
    for port, product, version, cves in version_to_cves(services, config or {}):
        lines.append(f"\n### {port}: {product} {version}\n{cves}")

    return "\n".join(lines)
