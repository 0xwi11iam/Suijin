"""
Supervisor — lightweight pattern-based oversight for the agent loop.

Runs every N iterations (default 5). Analyzes recent execution trace
for problematic patterns and silently injects corrective guidance.

Pattern-based (no LLM calls) — zero cost, instant execution.
"""

from __future__ import annotations

import logging
import re as _re_mod
from typing import Optional

# Phase transition config — defined inline
PHASE_TRANSITIONS = {
    "recon_to_scan": {"min_ports_discovered": 5, "min_services_identified": 3, "max_recon_iterations": 15},
    "scan_to_exploit": {"min_vulns_found": 1, "min_high_severity": 0, "max_scan_iterations": 20},
    "exploit_to_post": {"min_successful_exploits": 1, "min_flags_captured": 0, "max_exploit_iterations": 30},
}
PARALLEL_LIMITS = {
    "max_concurrent_scans": 4,
    "max_concurrent_exploits": 2,
    "max_concurrent_subagents": 3,
    "max_background_jobs": 8,
    "queue_timeout_seconds": 300,
}
RETRY_POLICY = {
    "max_retries": 3,
    "base_delay_seconds": 2,
    "max_delay_seconds": 60,
    "backoff_multiplier": 2.0,
    "jitter": True,
    "retry_on_status": [429, 500, 502, 503, 504],
}


def get_phase_config(phase: str) -> dict:
    for key, config in PHASE_TRANSITIONS.items():
        if phase.lower() in key:
            return config
    return {}


logger = logging.getLogger(__name__)

# Patterns that trigger supervisor intervention
# Each pattern: (name, detector_fn, guidance_template)


def _detect_repeating_tool(trace: list, threshold: int = 3) -> Optional[str]:
    """Detect if the same tool+args has been used 3+ times in a row."""
    if len(trace) < threshold:
        return None
    recent = trace[-threshold:]
    tools = [s.get("tool_name", "") for s in recent]
    if len(set(tools)) == 1 and tools[0]:
        return (
            f"You've called '{tools[0]}' {threshold} times in a row. "
            f"If it's not producing new results, STOP and try a DIFFERENT approach. "
            f"Switch to a different tool, different endpoint, or different attack vector entirely."
        )
    return None


def _detect_found_but_not_exploited(trace: list) -> Optional[str]:
    """Detect if a CONFIRMED vulnerability was left unexploited.

    v5.2 anti-interference: the old version keyword-matched the agent's own
    thought text ("checking for XSS" counted as a finding!) and its
    exploit-tools set missed the real arsenal — it fired mid-recon and
    mid-exploitation constantly. Now: only a CONFIRMED finding (strong
    claim language OR an actual record_finding call) counts, and any
    injection-class tool after it counts as exploitation in progress.
    """
    if len(trace) < 3:
        return None
    confirm_keywords = (
        "confirmed",
        "verified",
        "successfully exploit",
        "exploitation successful",
        "is vulnerable",
        "works —",
        "works:",
        "popped",
        "sqli confirmed",
        "rce confirmed",
    )
    finding_idx = -1
    for i in range(len(trace) - 1, -1, -1):
        if trace[i].get("tool_name") == "record_finding":
            finding_idx = i
            break
        thought = str(trace[i].get("thought", "")).lower()
        if any(kw in thought for kw in confirm_keywords):
            finding_idx = i
            break
    if finding_idx < 0:
        return None
    after_finding = trace[finding_idx + 1 :]
    if not after_finding:
        return None  # just found it this turn, give the agent a chance
    exploit_markers = (
        "http_request",
        "execute_terminal",
        "deploy_subagent",
        "sqlmap",
        "hydra",
        "ffuf",
        "gobuster",
        "nuclei",
        "payload_generate",
        "stager",
        "rev_shell",
        "jwt_forge",
        "custom_cmd_run",
        "recipe_run",
    )
    recent_after = [str(s.get("tool_name", "")) for s in after_finding[-3:]]
    if any(any(m in t for m in exploit_markers) for t in recent_after):
        return None  # exploitation is happening — do not interrupt
    return (
        "You confirmed a vulnerability but haven't followed through. "
        "TEST it NOW: http_request with the payload, execute_terminal with the "
        "exploit, or deploy_subagent for a focused pass."
    )


def _detect_bookkeeping_loop(trace: list, threshold: int = 4) -> Optional[str]:
    """Detect if the agent is stuck in a bookkeeping loop.

    v5.2: research tools (search_cve, web_search, read_file, write_file)
    were wrongly counted as 'bookkeeping' — an agent researching an exploit
    for four turns got slapped. Only true no-target-traffic bookkeeping
    counts now."""
    if len(trace) < threshold:
        return None
    recent_tools = [s.get("tool_name", "") for s in trace[-threshold:]]
    bookkeeping = {
        "write_note",
        "creds_add",
        "job_list",
        "job_status",
        "job_output",
        "check_knowledge",
        "record_finding",
    }
    if all(t in bookkeeping for t in recent_tools if t):
        return (
            f"You've spent {threshold} turns on bookkeeping without making progress. "
            f"STOP documenting. START exploiting. Run an actual attack tool NOW — "
            f"http_request with a payload, execute_terminal with an exploit, or "
            f"deploy_subagent for targeted attacks."
        )
    return None


def _detect_no_progress(trace: list, threshold: int = 5) -> Optional[str]:
    """Detect if no new information has been gained in N iterations.

    v5.2: 'duplicate' verdicts no longer count as no-progress — re-probing
    one endpoint with evolving payloads IS exploitation, and productivity's
    duplicate verdict fires constantly mid-chain (this detector was the top
    source of mid-exploitation interference)."""
    if len(trace) < threshold:
        return None
    recent = trace[-threshold:]
    no_progress = all(
        s.get("productivity", {}).get("verdict") in ("no_progress", "blocked") or not s.get("success", True)
        for s in recent
    )
    if no_progress:
        return (
            f"No progress in {threshold} iterations. You may be stuck in a loop. "
            f"RADICALLY change your approach: try a completely different attack vector, "
            f"a different port, a different tool. If the target has no more surface area, "
            f"generate your report and complete."
        )
    return None


def _detect_subagents_failing(trace: list, threshold: int = 3) -> Optional[str]:
    """Detect if subagents keep returning no findings."""
    if len(trace) < threshold:
        return None
    subagent_attempts = 0
    for s in trace[-6:]:
        thought = s.get("thought", "")
        if "subagent" in thought.lower() and (
            "returned no" in thought.lower() or "failed" in thought.lower() or "partial" in thought.lower()
        ):
            subagent_attempts += 1
    if subagent_attempts >= threshold:
        return (
            f"Subagents have failed {subagent_attempts} times. They are NOT working for this task. "
            f"Stop deploying subagents. Execute the task YOURSELF directly with http_request or "
            f"execute_terminal. You are more capable than a subagent."
        )
    return None


def _detect_missed_flag(trace: list) -> Optional[str]:
    """Detect if the agent found flag-like content but didn't claim it."""
    recent = trace[-3:]
    for s in recent:
        # Check both tool_output and thought for flag patterns
        output = str(s.get("tool_output") or "")
        thought = str(s.get("thought") or "")
        combined = (output + " " + thought).lower()
        if "flag{" in combined:
            tool_name = str(s.get("tool_name") or "")
            if "claim_flag" not in tool_name:
                return (
                    "You found a FLAG in the output but didn't claim it! "
                    "Use claim_flag IMMEDIATELY with the exact flag string. "
                    "Then record_finding and write_note about it."
                )
    return None


def _detect_phase_stall(trace: list, threshold: int = 20) -> Optional[str]:
    """Detect if agent is stuck in recon too long without exploiting."""
    if len(trace) < threshold:
        return None
    recent = trace[-threshold:]
    recon_tools = {
        "nmap",
        "gobuster",
        "ffuf",
        "feroxbuster",
        "amass",
        "whatweb",
        "subfinder",
        "httpx",
        "nikto",
        "sslscan",
        "shodan",
        "crtsh",
        "google_dork",
        "read_file",
        "web_search",
        "curl",
    }
    exploit_tools = {
        "http_request",
        "sqlmap_scan",
        "hydra",
        "execute_terminal",
        "deploy_subagent",
        "mcp_browser_goto",
        "msf_run",
    }
    recon_count = sum(1 for s in recent if s.get("tool_name", "") in recon_tools)
    exploit_count = sum(1 for s in recent if s.get("tool_name", "") in exploit_tools)
    if recon_count > 15 and exploit_count < 3:
        return (
            "FORCE EXPLOITATION: 15+ recon turns, <3 exploit attempts. "
            "You have enough data. TEST vulnerabilities NOW with http_request, "
            "mcp_browser_goto, or deploy_subagent with exploit tasks."
        )
    return None


def _detect_subagent_addiction(trace: list, threshold: int = 5) -> Optional[str]:
    """Detect when agent spawns subagents instead of working directly."""
    if len(trace) < threshold:
        return None
    recent_actions = [s.get("tool_name", "") for s in trace[-threshold:]]
    spawns = sum(1 for t in recent_actions if t == "deploy_subagent")
    direct = sum(
        1
        for t in recent_actions
        if t not in ("deploy_subagent", "write_note", "job_list", "job_status", "job_output", "check_knowledge")
    )
    if spawns >= 3 and direct < 2:
        return f"You spawned {spawns} subagents in {threshold} turns but did almost nothing yourself. Execute tools DIRECTLY."
    return None


def _detect_unverified_claim(trace: list) -> Optional[str]:
    """Detect when the agent claims a VERIFIED finding without evidence.

    v5.2 bug fix: the old check demanded 'diff_responses'/'diff_engine' —
    tools that DO NOT EXIST (the real tool is diff_response) — so it fired
    on every claimed finding and told the agent to use a phantom tool.
    Verification now accepts the real evidence tools: diff_response,
    evidence_capture, record_finding (with evidence), or a successful
    reproducing http_request/execute_terminal after the claim."""
    if len(trace) < 3:
        return None
    kw = ["SSTI confirmed", "SQLi found", "XSS detected", "RCE achieved", "vulnerability confirmed"]
    verify_tools = {"diff_response", "evidence_capture", "record_finding"}
    for i in range(len(trace) - 2, len(trace)):
        thought = str(trace[i].get("thought", "")).lower()
        if any(k.lower() in thought for k in kw):
            tools_since = [str(s.get("tool_name", "")) for s in trace[i:]]
            verified = any(t in verify_tools for t in tools_since)
            if not verified:
                # a successful exploit-class call after the claim IS evidence
                later = trace[i + 1 :]
                if any(
                    s.get("success") and str(s.get("tool_name", "")) in ("http_request", "execute_terminal")
                    for s in later
                ):
                    continue
                return (
                    "You claimed a confirmed finding — back it with evidence: re-run the "
                    "payload via diff_response, or evidence_capture the request/response pair."
                )
    return None


# ── Main supervisor ───────────────────────────────────────────────────


# ── Tactical follow-up library (v5.2) ────────────────────────────────────
# The supervisor's job is not only to catch pathology — it is to be a
# battle-buddy that spots MISSED OPPORTUNITIES. Each entry: a SIGNAL seen
# in a recent tool output and the FOLLOW-UP tools that should have come
# after it. Fires at most once per id per engagement window, never during
# pathology, never more than one per check.

TACTICAL_FOLLOWUPS = [
    # ── recon → deeper recon ──────────────────────────────────────────
    {
        "id": "robots-not-fetched",
        "signal": r"(?i)user-agent:\s*\*",
        "followups": ("http_request",),
        "hint": "robots.txt found with directives — fetch every Disallow path it names; they are the interesting ones.",
    },
    {
        "id": "sitemap-not-parsed",
        "signal": r"(?i)<\?xml[^>]*>(?s:.{0,200})?<urlset",
        "followups": ("parse_sitemap", "http_request"),
        "hint": "sitemap.xml found — parse it for the full route list before fuzzing blindly.",
    },
    {
        "id": "openapi-missing",
        "signal": r"(?i)\b(api|/api/v\d)/",
        "followups": ("openapi_find", "openapi_parse", "http_request"),
        "hint": "API surface seen — check /openapi.json, /swagger.json, /api-docs before brute-forcing endpoints.",
    },
    {
        "id": "graphql-missing",
        "signal": r"(?i)graphql",
        "followups": ("graphql_introspect", "graphql_probe"),
        "hint": "GraphQL mentioned — run graphql_introspect; introspection is often open and maps the whole schema.",
    },
    {
        "id": "bundle-not-mined",
        "signal": r"assets/[A-Za-z0-9_-]+\.js",
        "followups": ("js_bundle_analyze", "source_map_probe"),
        "hint": "JS bundle referenced — js_bundle_analyze it for routes/secrets/providers in one call.",
    },
    {
        "id": "sourcemap-ref",
        "signal": r"sourceMappingURL=",
        "followups": ("source_map_probe",),
        "hint": "Bundle references a sourcemap — source_map_probe may recover the full original source tree.",
    },
    {
        "id": "jwt-not-inspected",
        "signal": r"\beyJ[A-Za-z0-9_-]{20,}\.",
        "followups": ("jwt_inspect", "jwt_decode", "jwt_crack"),
        "hint": "JWT spotted — jwt_inspect it: algorithm, claims, expiry; weak secrets fall to jwt_crack.",
    },
    {
        "id": "version-no-cve",
        "signal": r"(?i)server:\s*[a-z-]+/\d",
        "followups": ("search_cve", "cve_search_nvd"),
        "hint": "Version banner disclosed — search_cve it before hand-crafting exploits.",
    },
    {
        "id": "port-no-fingerprint",
        "signal": r"(?i)(\d{1,3}\.){3}\d{1,3}\s+open",
        "followups": ("whatweb_scan", "nmap_scan", "sslscan_check"),
        "hint": "Open ports listed but not fingerprinted — whatweb/nmap -sV the interesting ones to get service versions.",
    },
    {
        "id": "cname-takeover",
        "signal": r"(?i)cname\s+(vercel|github|heroku|netlify|azure|cloudfront|fastly)",
        "followups": ("takeover_fingerprint", "dns_enum_nameservers"),
        "hint": "CNAME to a paas provider — takeover_fingerprint it; dangling aliases are free subdomain takeovers.",
    },
    {
        "id": "s3-name-seen",
        "signal": r"(?i)[a-z0-9.-]+\.s3[.-](amazonaws|website).{0,10}",
        "followups": ("bucket_check", "aws_s3"),
        "hint": "S3 bucket name spotted — bucket_check for public list/get/put.",
    },
    {
        "id": "cloud-meta-ssrfable",
        "signal": r"(?i)(169\.254\.169\.254|metadata\.google)",
        "followups": ("cloud_metadata_probe", "ssrf_blind_probe", "ssrf_canary"),
        "hint": "Metadata endpoint reachable/referenced — cloud_metadata_probe for instance credentials.",
    },
    {
        "id": "subdomain-vhost",
        "signal": r"(?i)\*\.[a-z0-9.-]+\.(com|net|org|io|dev|app)",
        "followups": ("vhost_check", "crtsh_subdomains", "subfinder_enum"),
        "hint": "Wildcard DNS — enumerate subdomains (crtsh/subfinder) then vhost_check the edge.",
    },
    {
        "id": "dir-listing-found",
        "signal": r"(?i)index of /",
        "followups": ("http_download", "gobuster_dir", "backup_file_probe"),
        "hint": "Directory listing exposed — walk it and probe for backups (.bak, .old, .zip, .sql).",
    },
    {
        "id": "backup-ext",
        "signal": r"(?i)\.(bak|old|orig|save|swp)(\?|\s|$)",
        "followups": ("backup_file_probe", "http_download"),
        "hint": "Backup file extension seen — probe sibling paths for source/config backups.",
    },
    {
        "id": "git-dir",
        "signal": r"(?i)(/\.git/|ref: refs/)",
        "followups": ("archive_extract", "http_download"),
        "hint": "Exposed .git — download the objects and reconstruct the source tree.",
    },
    {
        "id": "env-file",
        "signal": r"(?i)(\.env|DATABASE_URL=|AWS_SECRET_ACCESS_KEY=)",
        "followups": ("evidence_capture", "creds_add"),
        "hint": "Environment/config leak — capture evidence and store the creds for reuse.",
    },
    {
        "id": "cors-wildcard",
        "signal": r"(?i)access-control-allow-origin:\s*\*",
        "followups": ("cors_check",),
        "hint": "Wildcard CORS — cors_check whether credentials are also allowed (that combination is the bug).",
    },
    {
        "id": "spf-softfail",
        "signal": r"(?i)v=spf1.{0,80}~all",
        "followups": ("email_security_records", "dork_search"),
        "hint": "SPF softfail — the domain is spoofable; check DKIM/DMARC before moving on.",
    },
    {
        "id": "security-txt",
        "signal": r"(?i)contact:.*security",
        "followups": ("security_txt_check",),
        "hint": "security.txt found — read it for scope/hints/known-test accounts.",
    },
    {
        "id": "ws-endpoint",
        "signal": r"(?i)(wss?://|upgrade:\s*websocket)",
        "followups": ("ws_connect",),
        "hint": "WebSocket endpoint — connect and watch for auth-less message channels.",
    },
    {
        "id": "form-not-probed",
        "signal": r"(?i)<form[^>]*action=",
        "followups": ("http_request", "extract_forms", "mcp_browser_snapshot"),
        "hint": "HTML form found — extract every field and test each for injection/IDOR, not just the obvious one.",
    },
    {
        "id": "login-no-defaults",
        "signal": r"(?i)(login|signin|auth).{0,30}(form|page|post)",
        "followups": ("hydra_brute", "medusa_brute", "http_request"),
        "hint": "Login found — try default cred pairs (admin/admin, admin/password) before heavy brute force.",
    },
    {
        "id": "upload-found",
        "signal": r"(?i)(type=.?file.?|multipart/form-data)",
        "followups": ("http_request",),
        "hint": "File upload found — test extension bypass (.php5, .phtml, .js, .svg), content-type lies, and path traversal in the filename.",
    },
    {
        "id": "comment-leak",
        "signal": r"(?i)<!--\s*(todo|fixme|hack|temp|debug|remove)",
        "followups": ("extract_comments",),
        "hint": "Developer comments in HTML — extract_comments across the app; they name half-finished features.",
    },
    {
        "id": "tech-fingerprint-mismatch",
        "signal": r"(?i)x-powered-by:",
        "followups": ("whatweb_scan", "techfp"),
        "hint": "X-Powered-By leaked — fingerprint the exact framework version and search_cve it.",
    },
    # ── exploitation follow-through ───────────────────────────────────
    {
        "id": "google-key-unprobed",
        "signal": r"\bAIza[0-9A-Za-z_-]{35}\b",
        "followups": ("google_key_probe",),
        "hint": "Google API key found — google_key_probe it for active services (maps/translate/youtube) and referrer restrictions.",
    },
    {
        "id": "sqli-no-extraction",
        "signal": r"(?i)(sql syntax|unclosed quotation|\bor 1=1\b.{0,40}(true|accepted|logged in))",
        "followups": ("sqlmap_scan", "http_request"),
        "hint": "SQLi confirmed — extract data now: UNION columns, then table names; sqlmap --batch automates it.",
    },
    {
        "id": "xss-no-impact",
        "signal": r"(?i)(<script>alert|onerror=).{0,60}(reflected|executed|rendered)",
        "followups": ("http_request",),
        "hint": "XSS confirmed — escalate impact: steal session/CSRF token via fetch to your listener, or abuse stored context.",
    },
    {
        "id": "ssrf-no-oob",
        "signal": r"(?i)(ssrf (confirmed|works)|server (made|fetched) (a )?request)",
        "followups": ("ssrf_canary", "ssrf_blind_probe", "cloud_metadata_probe"),
        "hint": "SSRF confirmed — prove OOB with ssrf_canary (DNS/callback), then pivot to internal ports and metadata.",
    },
    {
        "id": "cmdi-no-shell",
        "signal": r"(?i)(command (injection|executed)|;\s*(id|whoami)\s*;?\s*uid=)",
        "followups": ("rev_shell", "stager", "execute_terminal"),
        "hint": "Command injection confirmed — upgrade to a shell: rev_shell/stager for your platform, then stabilize.",
    },
    {
        "id": "idor-no-enumerate",
        "signal": r"(?i)(idor|bola).{0,30}(confirmed|works|found)",
        "followups": ("http_request",),
        "hint": "IDOR confirmed — enumerate horizontally: script the id range via http_request and map every reachable object.",
    },
    {
        "id": "403-no-bypass",
        "signal": r"Status:\s*40[35]",
        "followups": ("verb_tamper", "host_header_inject", "http_request"),
        "hint": "403/405 wall — try verb tampering (PATCH/TRACE), path tricks (/admin;/, /%2e/), X-Original-URL, Host header rewrite.",
    },
    {
        "id": "creds-no-reuse",
        "signal": r"(?i)(password|credential)s? (found|dumped|harvested)",
        "followups": ("http_request", "hydra_brute", "medusa_brute"),
        "hint": "Credentials captured — test reuse immediately: same password against every login surface and SSH.",
    },
    {
        "id": "ssti-no-rce",
        "signal": r"(?i)(ssti (confirmed|works)|\{\{7\*7\}\}\s*=?\s*49)",
        "followups": ("http_request", "execute_terminal"),
        "hint": "SSTI confirmed — escalate to RCE for the exact engine (Jinja2/Twig/Freemarker each have a known chain), not just math.",
    },
    {
        "id": "xxe-no-file",
        "signal": r"(?i)xxe.{0,20}(confirmed|works)",
        "followups": ("http_request",),
        "hint": "XXE confirmed — read /etc/passwd, then aim at config/sources; wrap in error-based exfil if blind.",
    },
    {
        "id": "deser-no-poc",
        "signal": r"(?i)(deserializ|unserializ).{0,20}(confirmed|works|vulnerable)",
        "followups": ("payload_generate", "execute_terminal"),
        "hint": "Deserialization confirmed — craft the gadget chain (ysoserial / phar / pickle) for the exact stack.",
    },
    {
        "id": "jwt-none-unforged",
        "signal": r"(?i)alg.{0,6}none",
        "followups": ("jwt_forge_none", "jwt_forge_hs256"),
        "hint": "alg:none accepted — forge an admin token with jwt_forge_none and test privilege boundaries.",
    },
    {
        "id": "jwt-weak-secret",
        "signal": r"(?i)jwt.{0,20}(weak|crack|secret)",
        "followups": ("jwt_crack", "jwt_forge_hs256"),
        "hint": "JWT secret looks weak — jwt_crack it, then forge with your own claims.",
    },
    {
        "id": "race-condition",
        "signal": r"(?i)(coupon|balance|limit|quota).{0,30}(race|concurrent)",
        "followups": ("execute_terminal",),
        "hint": "State-changing endpoint with a limit — race it with parallel curls (-Z / xargs -P) before declaring it safe.",
    },
    {
        "id": "massassign-no-test",
        "signal": r"(?i)(role|isadmin|is_admin|admin)\s*[:=]",
        "followups": ("mass_assign_probe", "http_request"),
        "hint": "Role field in API payloads — mass_assign_probe registration/profile update with role=admin.",
    },
    {
        "id": "massassign-found",
        "signal": r"(?i)mass assignment.{0,20}(works|confirmed)",
        "followups": ("http_request",),
        "hint": "Mass assignment works — enumerate which fields bind (role, plan, price, team_id) and map the real impact.",
    },
    {
        "id": "nosql-not-tested",
        "signal": r"(?i)(mongodb|mongoose|couchdb|documentdb)",
        "followups": ("http_request",),
        "hint": "NoSQL backend — send operator injection ({'$ne': null}, '$regex', '$where') not just SQL syntax.",
    },
    {
        "id": "ldap-not-tested",
        "signal": r"(?i)(ldap|active directory|domain controller)",
        "followups": ("http_request", "ad_ldap_search"),
        "hint": "LDAP/AD environment — test filter injection (*)(|(&, attribute wildcards) on the login/search endpoints.",
    },
    {
        "id": "redis-exposed",
        "signal": r"(?i)redis.{0,20}(6379|found|exposed)",
        "followups": ("redis_info", "execute_terminal"),
        "hint": "Redis reachable — INFO dump, then the classic write-cron/ssh-key/module paths for RCE.",
    },
    # ── post-exploitation ─────────────────────────────────────────────
    {
        "id": "foothold-no-privesc",
        "signal": r"(?i)(foothold|shell obtained|initial access)",
        "followups": ("sudo_available", "execute_terminal"),
        "hint": "Foothold established — check privesc basics: sudo -l, SUID bits, writable services, kernel version.",
    },
    {
        "id": "db-access-no-dump",
        "signal": r"(?i)(database|mysql>|psql>).{0,20}(connected|access)",
        "followups": ("execute_terminal",),
        "hint": "DB access — enumerate schema, dump the user/credential tables, check for other DBs on the host.",
    },
    {
        "id": "cloud-key-no-validate",
        "signal": r"\bAKIA[0-9A-Z]{16}\b",
        "followups": ("aws_identity", "aws_enum"),
        "hint": "AWS key captured — aws_identity for the principal, then aws_enum its actual permissions.",
    },
    {
        "id": "token-no-scope-check",
        "signal": r"(?i)access_token.??\s*[:=]",
        "followups": ("http_request",),
        "hint": "Access token captured — call the profile/admin endpoints with it; scope misses are findings.",
    },
    {
        "id": "hash-no-crack",
        "signal": r"(?i)\$[0-9][a-z]?\$[A-Za-z0-9./]{20,}",
        "followups": ("identify_hash", "john_crack", "hashcat_crack"),
        "hint": "Hash captured — identify_hash, then john/hashcat with the target-specific wordlist you built.",
    },
    {
        "id": "container-no-escape",
        "signal": r"(?i)(docker|container|k8s).{0,20}(shell|foothold)",
        "followups": ("docker_analyze", "escape_check"),
        "hint": "Inside a container — escape_check CapEff/mounts/cgroup and docker_analyze the socket.",
    },
    {
        "id": "secret-no-report",
        "signal": r"(?i)(secret|key|token).{0,15}(found|leaked)",
        "followups": ("record_finding", "evidence_capture"),
        "hint": "Secret material found — record_finding + evidence_capture it before moving on; the report needs the proof.",
    },
]

_TACTICAL_COOLDOWN: dict[str, float] = {}
_TACTICAL_TTL = 1200.0  # seconds an id stays quiet after firing once
for _e in TACTICAL_FOLLOWUPS:  # compile once at import
    _e["signal"] = _re_mod.compile(_e["signal"])


def _tactical_check(trace: list) -> Optional[str]:
    """Signal-seen-but-followup-missing scan over recent tool outputs.
    Returns at most one guidance per check; never repeats an id."""
    import time as _time

    now = _time.monotonic()
    for _id in [k for k, ts in _TACTICAL_COOLDOWN.items() if now - ts > _TACTICAL_TTL]:
        _TACTICAL_COOLDOWN.pop(_id, None)
    for i, step in enumerate(trace):
        out = str(step.get("tool_output", ""))
        if not out:
            continue
        for entry in TACTICAL_FOLLOWUPS:
            eid = entry["id"]
            if eid in _TACTICAL_COOLDOWN:
                continue
            if entry["signal"].search(out):
                tools_after = [str(s.get("tool_name", "")) for s in trace[i + 1 :]]
                if any(any(f in t for f in entry["followups"]) for t in tools_after):
                    continue  # follow-up already done
                _TACTICAL_COOLDOWN[eid] = now
                return entry["hint"]
    return None


# ── Main supervisor ───────────────────────────────────────────────────


# v5.2 anti-interference: per-detector cooldown — the same nag repeated
# every check was derailing mid-exploitation runs. A detector that fired
# stays quiet for _DETECTOR_COOLDOWN_ITERS iterations.
_DETECTOR_COOLDOWN_ITERS = 15
_last_fired: dict[str, float] = {}


def analyze_trace(trace: list, iteration: float | None = None, **extra_kw) -> Optional[str]:
    """Analyze recent execution trace and return guidance if intervention needed.

    Args:
        trace: List of execution step dicts (last 10-15 entries).
        iteration: current iteration (float epochs not required — any
            increasing counter works; None = no cooldown tracking).
        extra_kw: Additional state data for context-aware detectors.

    Returns:
        Guidance string if intervention needed, None otherwise.
    """
    if not trace:
        return None

    detectors = [
        _detect_missed_flag,
        _detect_phase_stall,
        _detect_repeating_tool,
        _detect_bookkeeping_loop,
        _detect_found_but_not_exploited,
        _detect_subagent_addiction,
        _detect_subagents_failing,
        _detect_unverified_claim,
        _detect_no_progress,
        _detect_dead_end,
        _detect_payload_class_escalation,
    ]

    for detector in detectors:
        name = detector.__name__
        if iteration is not None and name in _last_fired and iteration - _last_fired[name] < _DETECTOR_COOLDOWN_ITERS:
            continue  # cooled down — the agent already heard this
        guidance = detector(trace, **extra_kw)
        if guidance:
            if iteration is not None:
                _last_fired[name] = iteration
            logger.info(f"Supervisor intervention: {name}")
            return guidance

    # pathology silent -> be a battle-buddy: one tactical opportunity hint
    # (cooldown-gated per id, so this cannot spam)
    try:
        return _tactical_check(trace)
    except Exception:  # noqa: BLE001 — heuristics must never break the loop
        return None


# ── LLM-Powered Deep Analysis ────────────────────────────────────────────────

_llm_supervisor_prompt = """You are a supervisor monitoring an autonomous security agent.
Review the last 10 execution steps and the current state. Identify:

1. MISSED OPPORTUNITIES: Did the agent find something but fail to exploit it?
2. INEFFICIENT PATTERNS: Is the agent stuck in a loop or wasting iterations?
3. STRATEGIC ERRORS: Is the agent using the wrong technique for the target?
4. PHASE MISMATCH: Is the agent doing recon when it should be exploiting?

Current phase: {phase}
Objective: {objective}
Total iterations so far: {iterations}
Cost so far: ${cost:.4f}

Recent execution trace:
{trace_summary}

Respond with EXACTLY ONE of:
- "NO_ISSUES" if everything looks correct
- A single concise guidance sentence (max 150 chars) if you see a problem

Your guidance will be injected as a supervisor message into the agent's next turn.
Be specific. Reference exact tool names, ports, or endpoints."""


async def analyze_trace_with_llm(
    trace: list,
    state: dict,
    generate_fn,
) -> str | None:
    """Use an LLM to perform deeper analysis of the agent's behavior.

    This runs AFTER pattern-based detection finds nothing. It catches
    subtle issues that regex patterns miss: strategic errors, missed
    chaining opportunities, inefficient tool selection.
    """
    if not trace or not generate_fn:
        return None

    lines = []
    for i, step in enumerate(trace[-10:], 1):
        tn = step.get("tool_name", "?")
        thought = str(step.get("thought", ""))[:120]
        success = "OK" if step.get("success", True) else "FAIL"
        lines.append(f"  {i}. [{tn}] ({success}) {thought}")

    trace_summary = "\n".join(lines)
    if len(trace_summary) > 2000:
        trace_summary = trace_summary[:2000] + "\n  ... (truncated)"

    prompt = _llm_supervisor_prompt.format(
        phase=state.get("current_phase", "informational"),
        objective=str(state.get("original_objective", ""))[:200],
        iterations=state.get("current_iteration", 0),
        cost=state.get("total_cost_usd", 0.0),
        trace_summary=trace_summary,
    )

    try:
        response = await generate_fn(
            model_id=None,
            prompt=prompt,
            system="You are a concise security supervisor. Respond with one line.",
            max_tokens=150,
            temperature=0.1,
        )
    except Exception:
        return None

    if not response or "NO_ISSUES" in response.upper():
        return None

    guidance = response.strip().strip('"').strip("'")
    if len(guidance) > 250:
        guidance = guidance[:250] + "..."
    return guidance


# ── Wave 2 detectors (A6 dead-end, A9 payload-class escalation) ────────


def _detect_dead_end(trace: list, fail_threshold: int = 3) -> Optional[str]:
    """A6: same tool FAILING repeatedly with varying args — a dead end.
    Distinct from _detect_repeating_tool (same args, any outcome): here
    the agent IS varying its approach inside one tool and still losing.
    The fix is not another variant — it's a different strategy CLASS.
    """
    if len(trace) < fail_threshold:
        return None
    recent = trace[-fail_threshold:]
    tools = [s.get("tool_name", "") for s in recent]
    if len(set(tools)) != 1 or not tools[0]:
        return None
    if all(not s.get("success", True) for s in recent):
        return (
            f"DEAD END: '{tools[0]}' failed {fail_threshold} times in a row with different inputs. "
            "Do not call it again with minor variations. Switch strategy CLASS entirely: "
            "different attack surface, different recon angle, or use deploy_subagent for a fresh pass. "
            "Write a note about why this path is blocked, then move on."
        )
    return None


_INJECTION_TOOLS = {"http_request", "execute_terminal", "custom_cmd_run"}
_PAYLOAD_FAMILIES = {
    "reflected": ("' OR 1=1", "<script>", "onerror=", "../../"),
    "blind": ("SLEEP(", "WAITFOR", "BENCHMARK(", "pg_sleep"),
    "timing": ("sleep", "waitfor", "delay"),
}


def _detect_payload_class_escalation(trace: list, fail_threshold: int = 4) -> Optional[str]:
    """A9: injection-style failures repeating — escalate the PAYLOAD CLASS
    (reflected -> blind -> timing/oob), not just the payload string."""
    if len(trace) < fail_threshold:
        return None
    recent = trace[-fail_threshold:]
    inj = [s for s in recent if s.get("tool_name") in _INJECTION_TOOLS]
    if len(inj) < fail_threshold or any(s.get("success") for s in inj):
        return None
    fam = set()
    for s in inj:
        args = str(s.get("tool_args", ""))
        for name, markers in _PAYLOAD_FAMILIES.items():
            if any(m.lower() in args.lower() for m in markers):
                fam.add(name)
    hint = ""
    if fam and fam <= {"reflected"}:
        hint = " You keep testing REFLECTED variants — escalate to BLIND (boolean/time-based) or OUT-OF-BAND (DNS/callback canaries via ssrf_canary)."
    elif fam and "reflected" in fam and "blind" in fam:
        hint = " You've tried reflected and blind — move fully to TIMING-based or OOB confirmation, or accept the surface is hardened."
    return (
        f"PAYLOAD CLASS ESCALATION: {fail_threshold} injection attempts failed. "
        "Varying the payload string is not working." + hint
    ) or None


def _confidence_from_decision(decision: dict) -> str:
    """A8 helper: normalize a decision's confidence claim."""
    raw = str(decision.get("confidence", "")).lower()
    if raw in ("verified", "confirmed", "certain"):
        return "verified"
    if raw in ("probable", "likely", "high"):
        return "probable"
    if raw in ("suspected", "possible", "low", "maybe"):
        return "suspected"
    return "probable"  # default when unclaimed — findings are never 'verified' without proof
