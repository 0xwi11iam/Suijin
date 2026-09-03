"""tester_fleet — the 8 dedicated vulnerability testers (Wave C).

Each tester is a doctrine fragment consumed as a fireteam task
attachment. select_lanes() is the ENGINEERED selection table —
pattern→lanes with priorities, the injection floor rule, form
synthesis. dispatch_testers() builds fireteam tasks that pass the
deploy gates and carry the doctrine + context block.
"""

from __future__ import annotations

import json
import re

TESTER_DOCTRINES = {
    "idor": (
        "IDOR/BOLA tester. Consume web_session(action=summary) for the cross-credential worklist. "
        "For each endpoint shape with ID fields differing by credential: replay with the OTHER "
        "credential's ID value using http_replay compare mode (baseline = your ID, exploit = theirs). "
        "A submitted user/account/owner id DISTINCT from the credential being authenticated satisfies "
        "the IDOR test even on login/registration steps. 404-consistent = NOT vulnerable (demote). "
        "Record findings ONLY on measurable response diff (status, body, redirect). "
        "Mark coverage: coverage_check(action=mark, vuln_class=idor, ...) with evidence."
    ),
    "authz": (
        "Authorization tester. Test 5 bypass patterns: (1) auth removal — Bearer null/undefined/admin, "
        "(2) lower-privilege replay across all registered credentials, (3) method override — "
        "X-HTTP-Method-Override, (4) path tricks — /ADMIN/x, /./admin/x, /api/v2/../admin/x, "
        "(5) param injection — ?admin=true&role=admin. FP RULES: 403/401 = enforcement WORKING, "
        "NOT a vulnerability. Both-200 with role-appropriate data = NOT a vulnerability. "
        "Cache-verify by adding a cache-buster param. Use http_replay compare with credential swap. "
        "Mark coverage: coverage_check(action=mark, vuln_class=authz, ...) with evidence."
    ),
    "mass-assignment": (
        "Mass-assignment tester. Target POST/PUT/PATCH endpoints with JSON bodies. Inject sensitive "
        "fields from web_session objects: role/is_admin/admin/permissions (escalation), price/amount/"
        "balance (financial: 0, -100, 999999), created_by (ownership). Casing variants: role/Role/ROLE, "
        "isAdmin/is_admin/is-admin. Ops: body-set-field (add ONE field, keep rest). ALWAYS verify "
        "persistence (GET after POST/PUT). GET/HEAD/OPTIONS = not applicable, skip. "
        "Mark coverage: coverage_check(action=mark, vuln_class=mass_assignment, ...) with evidence."
    ),
    "injection": (
        "Injection tester — SQLi + XSS + SSTI on EVERY free-text field, unconditionally. "
        "THE FLOOR: any text field is BOTH a DB/query sink AND a reflection/template sink — you "
        "CANNOT tell from outside whether it reaches a database. "
        "Step 1: inject_probe(vuln_class=sqli, field=X) for error fingerprints + boolean pairs. "
        "Step 2: inject_probe(vuln_class=xss, field=X) for tag survival + sink context. "
        "Step 3: inject_probe(vuln_class=ssti, field=X) for product-discriminators. "
        "If WAF-blocked: payload_mutate for evasion variants, http_replay codec=tab. "
        "FILTERED != SAFE — 4-5 DISTINCT variations before concluding. "
        "NoSQL operators if document store plausible: name[$ne], $regex. "
        "Record only with http_replay compare diff as evidence. "
        "Mark coverage: coverage_check(action=mark, vuln_class=injection, ...) with evidence."
    ),
    "authn": (
        "Authentication tester. Sub-areas: login bypass (type-juggling array on secret param, "
        "NOT identity param), JWT (alg:none, RS256-HS256 confusion, weak-secret via jwt_crack, "
        "kid path traversal, claim manipulation, exp bypass), session management (fixation via "
        "URL params, cookie flags), password reset (predictable tokens, Host-header poisoning), "
        "MFA bypass (response manipulation). "
        "Auth MECHANISM is origin-wide (test once, record wide note via coverage_check "
        "action=note kind=wide); auth ENFORCEMENT is per-endpoint (test each). "
        "Mark coverage: coverage_check(action=mark, vuln_class=authn, ...) with evidence."
    ),
    "business-logic": (
        "Business-logic tester. Target transaction/financial/multi-step endpoints. Tests: "
        "negative/zero/decimal/MAX_INT values, coupon stacking via duplicate JSON keys, "
        "workflow step-skip, X-Forwarded-For rotation for rate limits, "
        "race conditions via concurrent requests (execute_terminal with a Python script). "
        "VERIFICATION BAR: you must verify server-side state change, not just a 200 OK. "
        "If you can only confirm the request was accepted but not the state, do NOT report. "
        "Mark coverage: coverage_check(action=mark, vuln_class=business-logic, ...) with evidence."
    ),
    "ssrf": (
        "SSRF tester. Target url/redirect/callback/webhook/import parameters. "
        "Test: 169.254.169.254 FIRST (cloud metadata), internal IP ranges "
        "(127.0.0.1, 10.x, 192.168.x), protocol smuggling (gopher://, dict://, file://), "
        "decimal-IP http://2130706433, redirect chains (SSRF via open redirect on same origin). "
        "Use http_replay for each probe. Evidence: fetched metadata content or callback to "
        "a listener you control. "
        "Mark coverage: coverage_check(action=mark, vuln_class=ssrf, ...) with evidence."
    ),
    "file-attacks": (
        "File-attack tester. Target multipart uploads and path parameters. "
        "Upload: extension blocklist bypass (.php to .phtml/.pyc/renamed), GIF89a polyglot headers, "
        "SVG with embedded JS, .htaccess AddType override. Verify the uploaded file is REACHABLE "
        "(fetch the served path — accepted is not served). "
        "LFI: known OS files with content-signatures x 11 traversal shapes. A blocked plain "
        "../ is where the test BEGINS — ....// ..;/ %2e%2e%2f ..%252f are the shapes that matter. "
        "Aim at the app's own config file, not just /etc/passwd. "
        "Mark coverage: coverage_check(action=mark, vuln_class=file-attacks, ...) with evidence."
    ),
}

_ID_RE = re.compile(r"/(\d+|[0-9a-f]{8,}|[a-z0-9_-]{20,})(/|$)", re.I)
_FINANCE_RE = re.compile(r"amount|total|price|currency|cart|coupon|payment|checkout|balance|credit|withdraw|deposit", re.I)
_RACE_RE = re.compile(r"transfer|redeem|vote|claim|withdraw|like|follow|buy|reserve", re.I)
_FILE_RE = re.compile(r"file|path|download|attachment|upload|multipart", re.I)
_URL_RE = re.compile(r"^url$|redirect|callback|webhook|import|fetch", re.I)
_AUTH_RE = re.compile(r"login|signin|auth|oauth|token|sso|register|password|reset|session", re.I)
_ADMIN_RE = re.compile(r"/admin|/manage|/internal|/system", re.I)
_TEXT_RE = re.compile(r"name|subject|message|comment|search|filter|query|title|description|content", re.I)


def select_lanes(url: str = "", method: str = "GET", params: list | None = None,
                 body_fields: list | None = None, session_creds: int = 0) -> list[tuple[str, str]]:
    """The pattern-to-lanes table. Returns [(lane, reason), ...] sorted by priority."""
    blob = f"{url} {' '.join(params or [])} {' '.join(body_fields or [])}"
    method_u = method.upper()
    lanes: list[tuple[str, int, str]] = []

    if _ID_RE.search(url) or any(_ID_RE.search(p) for p in (params or [])):
        lanes.append(("idor", 90, "numeric/UUID identifiers in path/params"))
    if session_creds >= 2:
        lanes.append(("authz", 85, f"{session_creds} credentials in session"))
    if method_u in ("POST", "PUT", "PATCH") and body_fields:
        lanes.append(("mass-assignment", 80, f"{method_u} with body fields: {', '.join(body_fields[:4])}"))
    if _FINANCE_RE.search(blob):
        lanes.append(("business-logic", 85, "financial/transaction fields detected"))
    if _RACE_RE.search(blob):
        lanes.append(("business-logic", 80, "race-prone operation (transfer/redeem/claim)"))
    if _FILE_RE.search(blob):
        lanes.append(("file-attacks", 80, "file/path/upload parameters"))
    if _URL_RE.search(" ".join((params or []) + (body_fields or []))):
        lanes.append(("ssrf", 85, "URL/redirect/webhook parameter"))
    if _AUTH_RE.search(blob):
        lanes.append(("authn", 80, "authentication-related endpoint"))
    if _ADMIN_RE.search(url):
        lanes.append(("authz", 75, "admin/privileged path"))
    if any(_TEXT_RE.search(f) for f in (params or []) + (body_fields or [])):
        lanes.append(("injection", 70, "free-text field: SQLi+XSS+SSTI floor"))

    best: dict[str, tuple[str, int, str]] = {}
    for lane, pri, reason in lanes:
        if lane not in best or pri > best[lane][1]:
            best[lane] = (lane, pri, reason)
    result = sorted(best.values(), key=lambda x: -x[1])[:4]
    return [(lane, reason) for lane, _, reason in result]


def dispatch_testers(url: str = "", method: str = "GET", params: list | None = None,
                     body_fields: list | None = None, lanes: list | None = None,
                     max_lanes: int = 4) -> str:
    """Intelligent dispatch: select the right tester lanes for this request
    shape, build fireteam tasks with doctrine attached."""
    try:
        if not url:
            return "Error: url required"
        try:
            from suijin.modules.tools.lib.web_session import cross_credential_shortlist

            session_creds = len({c for s in cross_credential_shortlist() for c in s.get("credentials", [])})
        except Exception:  # noqa: BLE001
            session_creds = 0

        selected = lanes or [lane for lane, _ in select_lanes(url, method, params, body_fields, session_creds)]
        if not selected:
            selected = ["injection"]

        results = []
        for lane in selected[:max_lanes]:
            doctrine = TESTER_DOCTRINES.get(lane)
            if not doctrine:
                continue
            task = (
                f"Test {url} for {lane} vulnerabilities. {doctrine} "
                f"Use web_session(action=summary) for context. "
                f"Record findings via record_finding with evidence. "
                f"Mark coverage: coverage_check(action=mark, vuln_class={lane}, asset={url})."
            )
            results.append({"lane": lane, "task": task[:600], "coverage": f"coverage_check(action=mark, vuln_class={lane}, asset={url})", "deploy": f'deploy_subagent "{task[:200]}..."'})

        return json.dumps({
            "dispatch": len(results),
            "lanes": [r["lane"] for r in results],
            "tasks": results,
            "note": "Fire via deploy_subagent — doctrine attached, coverage targets set.",
        }, indent=2)[:5000]
    except Exception as e:  # noqa: BLE001
        return f"Error: dispatch_testers failed: {e}"
