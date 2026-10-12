"""UX tools for the AI — eliminating the 350 wasted iterations.

Each tool replaces a manual pattern the agent did by hand in the field:
- api_discover: replaces 121 browser_exec calls writing custom JS to find endpoints
- dom_recon: replaces the 5-6 snapshot+extract+parse cycle
- smart_fetch: replaces the 34 CORS-blocked → workaround → retry cycle
- jwt_decode: replaces 33 heredoc decodes
- response_diff: replaces 23 manual response comparisons
- collect_all: replaces 61 job polling cycles
- safe_exec: replaces 17 escaping-error iterations
- action_cache: replaces 56 repeated identical calls
"""

from __future__ import annotations

import hashlib
import json
import re
import time

# ═══════════════════════════════════════════════════════════════════
# 1. API DISCOVER — the understand-the-app tool (one call)
# ═══════════════════════════════════════════════════════════════════


def api_discover(url: str, click_interactive: bool = True, max_clicks: int = 8) -> str:
    """Discover the application's API surface in one call.

    Navigates to the URL, starts network capture, optionally clicks
    interactive elements (the AI decides — set click_interactive=False
    for read-only discovery), stops capture, and returns structured JSON:
    endpoints, configs, scripts, auth mechanisms.

    The AI should set click_interactive based on the target: True for
    exploration, False if the page has destructive actions (delete,
    logout, payment).
    """
    from suijin.modules.mcp_playwright.main import (
        mcp_browser_exec,
        mcp_browser_goto,
        mcp_browser_net_start,
        mcp_browser_requests,
        mcp_browser_snapshot,
    )

    # start capture
    mcp_browser_net_start()

    # navigate
    nav = mcp_browser_goto(url)
    if "Loaded" not in str(nav):
        return f"Error navigating to {url}: {nav}"

    # wait for SPA hydration
    time.sleep(2)

    # collect what the page loaded on its own
    mcp_browser_requests(limit=50)

    # optionally click interactive elements to discover more endpoints
    clicked_results = []
    if click_interactive:
        snap = mcp_browser_snapshot(max_elements=30)
        # parse snapshot lines for clickable elements
        # extract [N] tag "text" patterns
        elements = re.findall(r'\[(\d+)\]\s+(\w+)\s+"([^"]*)"', str(snap))
        for idx, tag, text in elements[:max_clicks]:
            # skip dangerous elements
            if any(
                w in text.lower()
                for w in ("delete", "remove", "logout", "sign out", "cancel", "payment", "buy", "submit payment")
            ):
                continue
            from suijin.modules.mcp_playwright.main import mcp_browser_click

            click_result = mcp_browser_click(f"[{idx}]")
            clicked_results.append(f"  clicked [{idx}] {tag} '{text[:30]}': {click_result[:60]}")
            time.sleep(0.5)

    # collect everything
    traffic = mcp_browser_requests(limit=100)

    # extract structured data from the page
    page_data = mcp_browser_exec("""
        (() => {
            const out = {configs: {}, scripts: [], forms: [], meta: []};
            // window config objects (CLIENT_CONFIG, SENTRY_CONFIG, etc.)
            for (const k of Object.keys(window)) {
                if (k.match(/^[A-Z_]{4,}$/) && typeof window[k] === 'object' && window[k] !== null) {
                    try {
                        const s = JSON.stringify(window[k]);
                        if (s && s.length < 2000 && !s.includes('function')) {
                            out.configs[k] = JSON.parse(s);
                        }
                    } catch(e) {}
                }
            }
            // script sources
            document.querySelectorAll('script[src]').forEach(s => out.scripts.push(s.src));
            // import maps
            const im = document.querySelector('script[type="importmap"]');
            if (im) {
                try { out.importMap = JSON.parse(im.textContent); } catch(e) {}
            }
            // forms with their fields
            document.querySelectorAll('form').forEach(f => {
                const fields = [];
                f.querySelectorAll('input,select,textarea').forEach(el => {
                    fields.push({name: el.name || el.id || '', type: el.type || el.tagName, hidden: el.type === 'hidden'});
                });
                if (fields.length) out.forms.push({action: f.action || '', method: f.method || 'GET', fields});
            });
            // meta tags
            document.querySelectorAll('meta[name],meta[http-equiv]').forEach(m => {
                out.meta.push({name: m.name || m.http-equiv, content: (m.content || '').slice(0, 100)});
            });
            return out;
        })()
    """)

    # parse the traffic into structured endpoints
    endpoints = []
    cookies = ""
    try:
        for line in str(traffic).splitlines():
            # parse "→ METHOD URL" and "← STATUS URL" patterns
            m = re.match(r"\s*[→←]\s+(?:(\w+)\s+)?(\S+)", line)
            if m:
                method_or_status = m.group(1) or ""
                url_part = m.group(2)
                if method_or_status in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                    endpoints.append({"method": method_or_status, "url": url_part, "source": "network"})
                elif method_or_status.isdigit() and endpoints:
                    endpoints[-1]["status"] = int(method_or_status)
    except Exception:
        pass

    # try to get cookies
    from suijin.modules.mcp_playwright.main import mcp_browser_cookies

    try:
        cookie_result = mcp_browser_cookies()
        cookies = str(cookie_result)[:500]
    except Exception:
        pass

    # build the result
    result = {
        "url": url,
        "endpoints": endpoints[:30],
        "configs": {},
        "scripts": [],
        "forms": [],
        "auth": {"cookies_present": bool(cookies and "[]" not in cookies)},
        "clicked": len(clicked_results),
    }

    # parse page_data if it's JSON
    try:
        pd = json.loads(str(page_data))
        result["configs"] = pd.get("configs") or {}
        result["scripts"] = (pd.get("scripts") or [])[:20]
        result["forms"] = pd.get("forms") or []
        result["importMap"] = pd.get("importMap")
    except Exception:
        pass

    # detect auth mechanism from configs/cookies
    if any("token" in str(v).lower() or "csrf" in str(v).lower() for v in result["configs"].values()):
        result["auth"]["mechanism"] = "csrf_token"
    elif "session" in cookies.lower() or "sess" in cookies.lower():
        result["auth"]["mechanism"] = "session_cookie"
    elif "bearer" in str(traffic).lower():
        result["auth"]["mechanism"] = "bearer"

    return json.dumps(result, indent=1, default=str)[:4000]


# ═══════════════════════════════════════════════════════════════════
# 2. DOM RECON — one comprehensive page scan
# ═══════════════════════════════════════════════════════════════════


def dom_recon() -> str:
    """One comprehensive DOM scan: forms (with all fields + hidden inputs),
    scripts (with src + inline configs), links, iframes, meta tags.
    Replaces the 5-6 snapshot→extract→parse cycle.
    """
    from suijin.modules.mcp_playwright.main import mcp_browser_exec

    result = mcp_browser_exec("""
        (() => {
            const out = {forms: [], scripts: [], links: [], iframes: [], metas: [], hiddenInputs: [], cookies: document.cookie.length};
            // forms with ALL fields
            document.querySelectorAll('form').forEach(f => {
                const fields = [];
                f.querySelectorAll('input,select,textarea,button').forEach(el => {
                    fields.push({
                        name: el.name || el.id || '',
                        type: el.type || el.tagName.toLowerCase(),
                        value: (el.type !== 'password' ? (el.value || '').slice(0, 50) : '***'),
                        hidden: el.type === 'hidden' || getComputedStyle(el).display === 'none',
                        required: el.required || false,
                    });
                });
                out.forms.push({action: f.action || '', method: (f.method || 'GET').toUpperCase(), fieldCount: fields.length, fields: fields.slice(0, 12)});
            });
            // scripts
            document.querySelectorAll('script').forEach(s => {
                if (s.src) out.scripts.push({src: s.src.slice(0, 120), type: s.type || 'js'});
                else if (s.textContent && s.textContent.length > 50) {
                    // inline script with potential config
                    const configMatch = s.textContent.match(/(?:window\\.)([A-Z_]{4,})\\s*=\\s*({[^}]{10,500}})/);
                    if (configMatch) out.scripts.push({inline: true, config: configMatch[1], data: configMatch[2].slice(0, 200)});
                }
            });
            // interesting links (not nav/footer boilerplate)
            document.querySelectorAll('a[href]').forEach(a => {
                const href = a.getAttribute('href');
                if (href && href !== '#' && !href.startsWith('javascript:') && !href.startsWith('mailto:')) {
                    if (href.includes('admin') || href.includes('api') || href.includes('debug') || href.includes('internal') || href.includes('config') || href.includes('.json') || href.includes('graphql')) {
                        out.links.push({href: href.slice(0, 100), text: (a.textContent || '').trim().slice(0, 40)});
                    }
                }
            });
            // iframes
            document.querySelectorAll('iframe').forEach(f => {
                out.iframes.push({src: (f.src || '').slice(0, 120), sandbox: f.sandbox ? f.sandbox.toString() : ''});
            });
            // security-relevant metas
            document.querySelectorAll('meta[http-equiv]').forEach(m => {
                out.metas.push({http_equiv: m.http_equiv, content: (m.content || '').slice(0, 150)});
            });
            // hidden inputs (CSRF tokens, session IDs, etc.)
            document.querySelectorAll('input[type="hidden"]').forEach(h => {
                out.hiddenInputs.push({name: h.name || h.id || '', value: (h.value || '').slice(0, 60)});
            });
            return out;
        })()
    """)

    try:
        parsed = json.loads(str(result))
        lines = [
            f"DOM RECON: {len(parsed.get('forms') or [])} forms, {len(parsed.get('scripts') or [])} scripts, {len(parsed.get('hiddenInputs') or [])} hidden inputs"
        ]

        for form in (parsed.get("forms") or [])[:5]:
            lines.append(
                f"\n  FORM {form.get('method')} {form.get('action', '(self)')} ({form.get('fieldCount')} fields)"
            )
            for f in (form.get("fields") or [])[:8]:
                hidden = " [hidden]" if f.get("hidden") else ""
                lines.append(f"    {f.get('type'):>10} {f.get('name', '?')}{hidden} = {f.get('value', '')}")

        for h in (parsed.get("hiddenInputs") or [])[:5]:
            lines.append(f"\n  HIDDEN: {h.get('name')} = {h.get('value')}")

        for s in (parsed.get("scripts") or [])[:10]:
            if s.get("src"):
                lines.append(f"  SCRIPT: {s['src']}")
            elif s.get("config"):
                lines.append(f"  CONFIG: {s['config']} = {s.get('data', '')[:80]}")

        for link in (parsed.get("links") or [])[:10]:
            lines.append(f"  LINK: {link.get('href')} — {link.get('text')}")

        for f in (parsed.get("iframes") or [])[:3]:
            lines.append(f"  IFRAME: {f.get('src')} (sandbox: {f.get('sandbox') or 'none'})")

        for m in (parsed.get("metas") or [])[:3]:
            lines.append(f"  META: {m.get('http_equiv')} = {m.get('content')}")

        return "\n".join(lines)
    except Exception:
        return str(result)[:2000]


# ═══════════════════════════════════════════════════════════════════
# 3. SMART FETCH — CORS-aware with automatic fallback
# ═══════════════════════════════════════════════════════════════════


def smart_fetch(url: str, method: str = "GET", body: str = "", headers: dict | None = None) -> str:
    """Fetch a URL with automatic CORS fallback. Tries in-page fetch first;
    if CORS-blocked, falls back to same-origin path, then to terminal curl.
    The agent never burns turns discovering CORS."""
    headers = headers or {}

    # try browser fetch first (has cookies, session, etc.)
    from suijin.modules.mcp_playwright.main import mcp_browser_exec

    js_headers = json.dumps(headers)
    js = f"""
        (async () => {{
            try {{
                const opts = {{method: '{method}', headers: {js_headers}, credentials: 'include'}};
                if ('{body}') {{ opts.body = '{body}'; }}
                const r = await fetch('{url}', opts);
                const text = await r.text();
                return JSON.stringify({{source: 'browser', status: r.status, body: text.slice(0, 2000), headers: Object.fromEntries(r.headers.entries())}});
            }} catch(e) {{
                if (e.message && e.message.includes('CORS')) {{
                    return JSON.stringify({{source: 'blocked', error: 'CORS', detail: e.message.slice(0, 100)}});
                }}
                return JSON.stringify({{source: 'error', error: e.message.slice(0, 100)}});
            }}
        }})()
    """
    result = mcp_browser_exec(js)

    try:
        parsed = json.loads(str(result))
        if parsed.get("source") == "browser":
            body_text = parsed.get("body", "")
            status = parsed.get("status", 0)
            resp_headers = parsed.get("headers", {})
            return f"Status: {status}\nHeaders: {json.dumps(resp_headers, indent=1)}\nBody:\n{body_text}"
        elif parsed.get("source") == "blocked":
            # CORS blocked → fall back to curl with the same cookies
            from suijin.modules.mcp_playwright.main import mcp_browser_cookies

            cookies_raw = mcp_browser_cookies()
            # extract cookie string
            cookie_str = ""
            try:
                cookies = json.loads(str(cookies_raw))
                if isinstance(cookies, list):
                    cookie_str = "; ".join(f"{c.get('name')}={c.get('value')}" for c in cookies if c.get("name"))
            except Exception:
                pass

            # build curl command
            curl_parts = ["curl -sS"]
            if method != "GET":
                curl_parts.append(f"-X {method}")
            for k, v in headers.items():
                curl_parts.append(f"-H '{k}: {v}'")
            if cookie_str:
                curl_parts.append(f"-H 'Cookie: {cookie_str[:200]}'")
            if body:
                curl_parts.append(f"-d '{body[:500]}'")
            curl_parts.append(f"'{url}'")

            from suijin.modules.tools.lib.terminal import execute_terminal

            curl_out = execute_terminal(" ".join(curl_parts), timeout=15)
            return f"(CORS fallback to curl)\n{curl_out}"
        else:
            return f"Browser fetch error: {parsed.get('error', 'unknown')}"
    except Exception:
        # not JSON — return raw
        return str(result)[:2000]


# ═══════════════════════════════════════════════════════════════════
# 4. JWT DECODE — replaces 33 heredoc decodes
# ═══════════════════════════════════════════════════════════════════


def jwt_decode(token: str) -> str:
    """Decode a JWT without verifying. Returns header, payload, and
    human-readable claims (expiry, role, user_id, issued-at)."""
    import base64

    token = token.strip()
    parts = token.split(".")
    if len(parts) != 3:
        return f"Error: not a JWT (expected 3 parts, got {len(parts)}). JWTs look like: eyJ...eyJ...signature"

    def _b64decode(s: str) -> dict:
        # add padding
        s += "=" * (4 - len(s) % 4)
        raw = base64.urlsafe_b64decode(s)
        return json.loads(raw)

    try:
        header = _b64decode(parts[0])
        payload = _b64decode(parts[1])
    except Exception as e:
        return f"Error decoding JWT: {e}"

    lines = ["JWT decoded:"]
    lines.append(f"  header: {json.dumps(header)}")
    lines.append(f"  algorithm: {header.get('alg', '?')}")
    lines.append(f"  type: {header.get('typ', '?')}")

    lines.append("\n  payload:")
    for k, v in sorted(payload.items()):
        # human-readable for common claims
        if k in ("exp", "iat", "nbf") and isinstance(v, (int, float)):
            from datetime import datetime, timezone

            dt = datetime.fromtimestamp(v, tz=timezone.utc)
            expired = " (EXPIRED)" if k == "exp" and v < time.time() else ""
            lines.append(f"    {k}: {v} → {dt.strftime('%Y-%m-%d %H:%M:%S UTC')}{expired}")
        elif k in ("role", "roles", "permissions", "scope", "admin", "is_admin", "user_type"):
            lines.append(f"    {k}: {v}  ← PRIVILEGE CLAIM")
        elif k in ("sub", "user_id", "uid", "id"):
            lines.append(f"    {k}: {v}  ← IDENTITY")
        else:
            lines.append(f"    {k}: {v}")

    if payload.get("exp") and payload["exp"] < time.time():
        lines.append(f"\n  ⚠ TOKEN IS EXPIRED (exp: {payload['exp']})")
    if header.get("alg") == "none":
        lines.append("\n  ⚠ ALGORITHM IS 'none' — signature may be optional")

    lines.append(f"\n  signature: {parts[2][:20]}...")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 5. RESPONSE DIFF — replaces 23 manual comparisons
# ═══════════════════════════════════════════════════════════════════


def response_diff(
    url_a: str, url_b: str, method: str = "GET", body_a: str = "", body_b: str = "", headers: dict | None = None
) -> str:
    """Fetch two URLs (or the same URL with different bodies/headers) and
    diff the responses. Shows: status difference, header differences,
    body differences. The A/B testing tool."""
    from suijin.modules.tools.lib.terminal import execute_terminal

    headers = headers or {}
    header_args = " ".join(f"-H '{k}: {v}'" for k, v in headers.items())

    cmd_a = f"curl -sS -D - {header_args} {'-d ' + repr(body_a) if body_a else ''} -X {method} '{url_a}' 2>&1"
    cmd_b = f"curl -sS -D - {header_args} {'-d ' + repr(body_b) if body_b else ''} -X {method} '{url_b}' 2>&1"

    out_a = execute_terminal(cmd_a, timeout=15)
    out_b = execute_terminal(cmd_b, timeout=15)

    # parse status + headers + body
    def _parse(resp: str) -> dict:
        status = 0
        headers = {}
        body = ""
        lines = resp.splitlines()
        for i, ln in enumerate(lines):
            if ln.startswith("HTTP/"):
                status = int(ln.split()[1]) if len(ln.split()) > 1 else 0
            elif ": " in ln and i < 30:
                k, v = ln.split(": ", 1)
                headers[k.strip()] = v.strip()
            elif ln == "" and i > 0:
                body = "\n".join(lines[i + 1 :])
                break
        return {"status": status, "headers": headers, "body": body}

    a, b = _parse(out_a), _parse(out_b)

    lines = [f"RESPONSE DIFF: {method} {url_a[:60]} vs {url_b[:60]}"]

    if a["status"] != b["status"]:
        lines.append(f"  STATUS: {a['status']} vs {b['status']}  ← DIFFERENT")
    else:
        lines.append(f"  STATUS: {a['status']} (same)")

    # header differences
    all_headers = set(a["headers"]) | set(b["headers"])
    header_diffs = []
    for h in sorted(all_headers):
        va, vb = a["headers"].get(h, "(absent)"), b["headers"].get(h, "(absent)")
        if va != vb:
            header_diffs.append(f"    {h}: {va[:50]} vs {vb[:50]}")
    if header_diffs:
        lines.append(f"  HEADERS ({len(header_diffs)} differences):")
        lines.extend(header_diffs[:10])
    else:
        lines.append("  HEADERS: identical")

    # body differences
    if a["body"] != b["body"]:
        lines.append("  BODY: DIFFERENT")
        lines.append(f"    A ({len(a['body'])} chars): {a['body'][:100]}")
        lines.append(f"    B ({len(b['body'])} chars): {b['body'][:100]}")
    else:
        lines.append("  BODY: identical")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 6. COLLECT ALL — one call for everything finished
# ═══════════════════════════════════════════════════════════════════


def collect_all() -> str:
    """Collect ALL finished background jobs + fireteam results + mesh
    notifications in one call. No more polling individual jobs."""
    parts = []

    # finished jobs
    from suijin.modules.tools.lib import job_registry as jr

    try:
        for msg in jr.collect_finished_jobs(max_results=10):
            parts.append(f"JOB: {msg}")
    except Exception:
        pass

    # fireteam results
    try:
        from suijin.modules.agent.lib.nodes.subagent_node import collect_finished_teams

        for msg in collect_finished_teams():
            parts.append(f"TEAM: {msg}")
    except Exception:
        pass

    # mesh notifications (drain asks)
    try:
        from suijin.modules.agent.lib import mesh

        asks = mesh.drain_asks()
        for ask in asks:
            parts.append(f"MESH ASK: {ask}")
    except Exception:
        pass

    if not parts:
        return "Nothing finished. All background jobs and fireteams are still running."

    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
# 7. ACTION CACHE — prevents repeated identical calls
# ═══════════════════════════════════════════════════════════════════

_action_cache: dict[str, dict] = {}
_MAX_CACHE = 200


def _cache_key(tool: str, args: dict) -> str:
    blob = f"{tool}:{json.dumps(args, sort_keys=True, default=str)}"
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def cache_check(tool: str, args: dict) -> str | None:
    """Check if this exact action was already performed. Returns the
    cached result or None if not seen."""
    key = _cache_key(tool, args)
    entry = _action_cache.get(key)
    if entry and time.time() - entry["at"] < 600:  # 10 min TTL
        return (
            f"ALREADY DONE: {tool} with these exact args was called "
            f"{int(time.time() - entry['at'])}s ago. Result was:\n"
            f"{entry['result'][:200]}\n"
            f"(Use different args, or force with the _force flag if you need a fresh result.)"
        )
    return None


def cache_record(tool: str, args: dict, result: str) -> None:
    """Record an action result for future dedup. Only SUCCESSFUL calls —
    failed calls need to be re-executed (and the repeat guard tracks
    failures separately)."""
    result_str = str(result)
    if result_str.startswith(("Error", "HTTP Error", "Tool Error", "SYSTEM OVERRIDE", "Command denied")):
        return  # don't cache failures
    key = _cache_key(tool, args)
    _action_cache[key] = {"at": time.time(), "result": result_str[:300]}
    if len(_action_cache) > _MAX_CACHE:
        # prune oldest
        oldest = sorted(_action_cache.items(), key=lambda kv: kv[1]["at"])
        for k, _ in oldest[: _MAX_CACHE // 2]:
            _action_cache.pop(k, None)


def cache_clear() -> None:
    """Clear the action cache (test hook)."""
    _action_cache.clear()
