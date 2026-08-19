"""CVE -> tool advisor (B12) + KB freshness (B18)."""

from __future__ import annotations

# vulnerability keywords -> the pack/tools that exploit or verify them
_CVE_TOOLS = {
    ("log4j", "log4shell"): ["searchsploit_find", "http_request"],
    ("spring4shell", "spring boot"): ["spring_boot_scan" if False else "http_request", "searchsploit_find"],
    ("sql injection", "sqli"): ["sqli_polyglots", "sqlmap_shell", "apifuzz"],
    ("xss", "cross-site scripting"): ["xss_polyglots", "extract_artifacts"],
    ("ssrf", "server-side request"): ["ssrf_canary", "cloud_metadata_probe"],
    ("rce", "remote code execution"): ["searchsploit_find", "payload_generate"],
    ("lfi", "path traversal", "directory traversal"): ["sqli_polyglots", "read_file"],
    ("deserialization",): ["searchsploit_find", "payload_generate"],
    ("redirect",): ["open_redirect_check", "redirtrace"],
    ("takeover",): ["takeover_fingerprint"],
    ("jwt", "session"): ["jwt_inspect", "jwtlab"],
    ("grafana",): ["http_request", "cve_search_nvd"],
    ("swagger", "openapi"): ["openapi_find", "openapi_parse"],
}


def advise_tools(cve_or_kw: str) -> str:
    """Map a CVE id/description to the tools that verify or exploit it."""
    low = (cve_or_kw or "").lower()
    if not low.strip():
        return "Error: keyword or CVE id required"
    hits = set()
    for keys, tools in _CVE_TOOLS.items():
        if any(k in low for k in keys):
            hits.update(tools)
    if not hits:
        # generic fallback: search exploitdb + nvd
        return f"no curated mapping for '{cve_or_kw[:40]}' — start with: searchsploit_find, cve_search_nvd, then payload_generate"
    return "recommended tools: " + ", ".join(sorted(hits))


def kb_freshness(max_age_days: int = 30) -> str:
    """B18: KB age check with a re-pull prompt when stale."""
    from suijin.modules.knowledge.lib.kb import kb_status

    st = kb_status()
    if not st:
        return "KB not built — run: suijin pull kb"
    age = st.get("age_days")
    if age is None:
        return f"KB: {st['docs']:,} docs (age unknown — consider a re-pull)"
    if age > max_age_days:
        return f"KB STALE: {age}d old ({st['docs']:,} docs) — run 'suijin pull kb' to refresh before relying on it"
    return f"KB fresh: {age}d old, {st['docs']:,} docs across {st['sources']} sources"
