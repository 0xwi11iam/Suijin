"""tool_inventory — a human pentester knows their kit; so should the agent.

Field complaint (2026-10-29): "never seen AI using nmap, gobuster,
katana…" — the tools were installed and ROUTED but invisible: the agent's
catalog never listed them. This tool shows the complete picture:
  1. every catalog tool (the in-process toolkit)
  2. every classic binary actually ON PATH (capability CHECK, not a
     platform guess — absent binaries simply don't list), each with a
     one-line when-to-use doctrine
"""

from __future__ import annotations

import shutil

_BINARIES: list[tuple[str, str]] = [
    ("nmap", "port/service/version baseline — the recon first move"),
    ("gobuster", "dir/dns brute-force (gobuster_dir/gobuster_dns wrap it)"),
    ("katana", "JS-aware crawler (katana_crawl)"),
    ("nikto", "web-server config/misconfig scan"),
    ("whatweb", "tech fingerprint → search_cve the versions"),
    ("feroxbuster", "recursive content discovery"),
    ("nuclei", "template vuln scan — cve/exposures/misconfig"),
    ("ffuf", "the FUZZ-keyword fuzzer (ffuf_fuzz)"),
    ("sqlmap", "SQL injection automation — confirm manually first"),
    ("dirsearch", "web path discovery"),
    ("rustscan", "fast port sweep → feed open ports to nmap"),
    ("masscan", "internet-scale port scan"),
    ("testssl.sh", "TLS/SSL config audit"),
    ("jq", "JSON wrangling for API responses"),
    ("curl", "raw HTTP when http_request is overkill"),
    ("nuclei-template", "custom nuclei template runner"),
]


def tool_inventory(*_args, **_kwargs) -> str:
    """List the full kit: catalog tools + installed binaries + doctrine."""
    lines = ["=== YOUR TOOLKIT ===", ""]

    lines.append("-- Catalog tools (call directly) --")
    try:
        from suijin.modules.agent.lib.prompts.tool_registry import _ALL_TOOLS

        names = sorted(_ALL_TOOLS)
        lines.append(f"{len(names)} tools: {', '.join(names)}")
    except Exception as e:  # noqa: BLE001 — inventory must never fail hard
        lines.append(f"(catalog unavailable: {e})")

    lines.append("")
    lines.append("-- Installed binaries on PATH (use via the wrapper or execute_terminal) --")
    installed = []
    missing = []
    for name, doctrine in _BINARIES:
        path = shutil.which(name)
        (installed if path else missing).append(name)
        if path:
            lines.append(f"  {name:14s} — {doctrine}")
    if not installed:
        lines.append("  (none of the classic set installed — request via the tool manifest)")

    if missing:
        lines.append("")
        lines.append(f"-- Not installed here: {', '.join(missing)} --")
        lines.append("  (don't reach for these; the wrappers above handle the equivalents)")

    lines.append("")
    lines.append(
        "Stuck? The classic doctrine: nmap the host → whatweb the service → "
        "gobuster/ffuf the paths → nuclei the findings → catalog_exploit what confirms."
    )
    return "\n".join(lines)
