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
    from suijin.modules.tools.lib.intel import search_cve

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

    from suijin.modules.loader import get_module_tools

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

    # v5.2: whatweb fingerprint on HTTP services (banner-grab depth)
    web_fingerprint = _fingerprint_web(target, services)
    if web_fingerprint:
        lines.append("\n## Web fingerprint (whatweb)")
        lines.append(web_fingerprint)

    lines.append("\n## CVE matches (version-based)")
    for port, product, version, cves in version_to_cves(services, config or {}):
        lines.append(f"\n### {port}: {product} {version}\n{cves}")

    lines.append(_exploit_leads(services))
    return "\n".join(lines)


def _fingerprint_web(target: str, services: list[dict]) -> str:
    """Run whatweb on the first HTTP(S) port found (if the tool is available)."""
    from suijin.modules.loader import get_module_tools

    whatweb = get_module_tools().get("whatweb_scan")
    if whatweb is None:
        return ""
    for s in services:
        port = str(s.get("port", ""))
        svc = str(s.get("service", "")).lower()
        if "http" in svc or port in ("80", "443", "8080", "8443"):
            scheme = "https" if "https" in svc or port in ("443", "8443") else "http"
            url = f"{scheme}://{target}:{port}" if port not in ("80", "443") else f"{scheme}://{target}"
            try:
                out = whatweb(url=url)
                return str(out)[:2000]
            except Exception:  # noqa: BLE001 — fingerprinting is best-effort
                return ""
    return ""


def _exploit_leads(services: list[dict], max_leads: int = 3) -> str:
    """Offline KB exploit suggestions for the top fingerprinted services."""
    from suijin.modules.knowledge.lib.kb import DB_PATH

    if not DB_PATH.exists():
        return ""
    from suijin.modules.knowledge.lib.kb_tools import suggest_exploit

    seen, blocks = set(), []
    for s in services:
        service = (
            (s.get("banner") or s.get("service") or "").split()[0] if (s.get("banner") or s.get("service")) else ""
        )
        if not service or service.lower() in seen:
            continue
        seen.add(service.lower())
        try:
            res = suggest_exploit(service)
        except Exception:
            continue
        if "gtfobins" in res or "[hacktricks]" in res or "[payloads]" in res:
            head = res.split("\n", 1)[0]
            blocks.append(f"\n### {s['port']}: {head}\n" + "\n".join(res.splitlines()[1:12]))
        if len(blocks) >= max_leads:
            break
    if not blocks:
        return ""
    return "\n## Exploit leads (offline KB)\n" + "\n".join(blocks)
