"""JS-bundle attack-surface mining — SPA recon tools.

The spa-target.example field run showed the agent hand-rolling curl+grep
bundles across THREE iterations (with a broken PATHS regex that returned
nothing on the first try). These tools do it in one call each:

- js_bundle_analyze: download a JS bundle and mine it for API routes,
  absolute URLs, leaked secrets, auth providers, lazy chunks, sourcemaps
- google_key_probe: test a leaked Google API key against common services
  (read-only probes) and report which ones accept it
- source_map_probe: check for exposed .map files and list original sources
"""

from __future__ import annotations

import re

import requests

# module-boundary rule: cross-module imports stay function-local

# route-ish path literals inside JS: "/api/...", "/admin", "/v1/..." — both
# quote styles, minified or not. (The field-run hand regex died on escaping.)
PATH_RE = re.compile(
    r"[\"'`](/(?:api|auth|admin|v\d|user|users|account|login|logout|signin|signup|register|search|chat|file|files|document|documents|upload|dashboard|settings|profile|billing|internal|private|debug|graphql|rpc|rest)[a-zA-Z0-9/_{}$.:?=&-]*)[\"'`]"
)
URL_RE = re.compile(r"https?://[a-zA-Z0-9.-]+(?:\.[a-z]{2,})(?:/[a-zA-Z0-9/_{}$.:-]*)?")
SECRET_RES = [
    ("google-api-key", re.compile(r"\b(AIza[0-9A-Za-z_-]{35})\b")),
    ("openai-key", re.compile(r"\b(sk-[A-Za-z0-9_-]{20,})\b")),
    ("firebase-app", re.compile(r"\b([a-z0-9-]+\.firebaseapp\.com)\b")),
    ("supabase-url", re.compile(r"\b(https://[a-z0-9]+\.supabase\.co)\b")),
    ("supabase-anon-key", re.compile(r"\b(eyJ[a-zA-Z0-9_-]{20,}\.eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,})\b")),
    ("jwt", re.compile(r"\b(eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-z0-9_-]{10,})\b")),
    ("github-token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,})\b")),
    ("aws-key", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("google-oauth-client-id", re.compile(r"\b(\d{8,}-[a-z0-9]+\.apps\.googleusercontent\.com)\b")),
]
PROVIDER_RE = re.compile(
    r"(?i)\b(supabase|firebase|clerk|auth0|nextauth|cognito|okta|keycloak|stripe|sentry|datadog|segment|mixpanel|amplitude|intercom|launchdarkly|algolia|contentful|sanity|prisma|tesseract)\b"
)
CHUNK_RE = re.compile(r"[\"'`]([^\"'`\s]{1,60}-[A-Za-z0-9_-]{8}\.(?:js|css))[\"'`]")
SOURCEMAP_RE = re.compile(r"sourceMappingURL=([^\s}\"']+)")
_NOISE_URL = re.compile(
    r"(?i)w3\.org|reactjs|react\.dev|github\.com|npmjs|mozilla|localhost|127\.0\.0|schema|example\.com|rolldown|vitejs|projectnaptha|unsplash|fonts\.|googleapis\.com/css"
)


def _get(url: str, timeout: int = 20) -> requests.Response:
    from suijin.modules.platform.lib.stealth import browser_identity

    headers = browser_identity()
    headers.setdefault("Accept", "*/*")
    return requests.get(url, headers=headers, timeout=timeout, verify=False)


def js_bundle_analyze(url: str, max_len: int = 4_000_000) -> str:
    """Download a JS bundle and mine it for the app's real attack surface.

    Returns sections: ROUTES (api/admin/auth path literals), URLS (absolute,
    de-noised), SECRETS (with kind), PROVIDERS (auth/infra SaaS), CHUNKS
    (lazy-loaded assets), SOURCEMAPS (exposed .map refs).
    """
    if not url:
        return "Error: url required"
    try:
        r = _get(url)
    except Exception as e:  # noqa: BLE001 — transport errors are data
        return f"Error fetching bundle: {e}"
    body = r.text[:max_len]
    if not body.strip():
        return f"Empty body (status {r.status_code})"

    routes = sorted(set(PATH_RE.findall(body)))[:60]
    urls = sorted(u for u in set(URL_RE.findall(body)) if not _NOISE_URL.search(u))[:40]
    secrets: list[str] = []
    for kind, rx in SECRET_RES:
        for m in rx.findall(body):
            entry = f"{kind}: {m}" if kind != "private-key-block" else "private-key-block: (present)"
            if entry not in secrets:
                secrets.append(entry)
    providers = sorted({p.lower() for p in PROVIDER_RE.findall(body)})
    chunks = sorted(set(CHUNK_RE.findall(body)))[:30]
    sourcemaps = sorted(set(SOURCEMAP_RE.findall(body)))[:10]

    def _sec(title: str, items: list) -> list[str]:
        shown = [f"  {i}" for i in items] or ["  (none)"]
        return [f"== {title} ({len(items)}) =="] + shown

    lines = [
        f"bundle: {url}",
        f"status {r.status_code} · {len(r.text)} bytes"
        + (f" (analyzed first {max_len})" if len(r.text) > max_len else ""),
        "",
    ]
    lines += _sec("ROUTES", routes) + [""]
    lines += _sec("URLS", urls) + [""]
    lines += _sec("SECRETS", secrets) + [""]
    lines += ["== PROVIDERS =="] + ["  " + (" ".join(providers) if providers else "(none)")] + [""]
    lines += _sec("LAZY CHUNKS", chunks[:15]) + [""]
    lines += _sec("SOURCEMAP REFS", sourcemaps)
    return "\n".join(lines)


def google_key_probe(key: str) -> str:
    """Test a Google API key against common services (READ-ONLY probes).

    Reports which services accept the key — the follow-up to a leaked
    AIza... key found in a bundle. Probes: Maps geocode, Timezone,
    Drive about (needs OAuth — marked as such), reCAPTCHA siteverify
    shape, Youtube (data API often enabled), Translate v2.
    """
    if not key or not key.startswith("AIza"):
        return "Error: expected a Google API key (AIza...)"
    probes = [
        ("maps_geocode", "https://maps.googleapis.com/maps/api/geocode/json?address=1", {"key": key}),
        ("maps_timezone", "https://maps.googleapis.com/maps/api/timezone/json?location=0,0&timestamp=0", {"key": key}),
        (
            "translate_v2",
            "https://translation.googleapis.com/language/translate/v2",
            {"key": key, "q": "hi", "target": "en", "format": "text"},
        ),
        (
            "youtube_v3",
            "https://www.googleapis.com/youtube/v3/videos?part=id&chart=mostPopular&maxResults=1",
            {"key": key},
        ),
        ("customsearch", "https://www.googleapis.com/customsearch/v1?q=test&cx=000000000000000000000", {"key": key}),
    ]
    out = [f"google key probe: {key[:10]}...{key[-4:]}"]
    for name, url, params in probes:
        try:
            r = requests.get(url, params=params, timeout=10, verify=False)
            js = r.json() if "json" in r.headers.get("Content-Type", "") else {}
            if r.status_code == 200 and "error" not in js:
                out.append(f"  [ACTIVE]   {name} — key accepted (status 200)")
            else:
                err = (js.get("error") or {}).get("status", r.status_code)
                reason = (js.get("error") or {}).get("message", "")[:80]
                verdict = (
                    "REFERRER/IP-RESTRICTED" if err in ("REQUEST_DENIED", "PERMISSION_DENIED", 403) else "not enabled"
                )
                out.append(f"  [{verdict}] {name} — {err} {reason}")
        except Exception as e:  # noqa: BLE001
            out.append(f"  [error]    {name} — {e}")
    out.append("")
    out.append("next: keys restricted by HTTP referrer still work from the app's origin —")
    out.append("test in-context (browser MCP) before declaring dead.")
    return "\n".join(out)


def source_map_probe(url: str) -> str:
    """Check a JS/CSS asset for an exposed source map and list its sources.

    `url` is the asset URL; appends .map (and follows //# sourceMappingURL
    if present). Exposed maps recover the full original source tree."""
    if not url:
        return "Error: url required"
    try:
        r = _get(url)
    except Exception as e:  # noqa: BLE001
        return f"Error fetching asset: {e}"
    body = r.text[:1_000_000]
    ref = SOURCEMAP_RE.search(body)
    candidates = []
    if ref:
        ref_url = ref.group(1)
        candidates.append(ref_url if ref_url.startswith("http") else url.rsplit("/", 1)[0] + "/" + ref_url)
    candidates.append(url + ".map")
    seen = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        try:
            rm = _get(c)
        except Exception:  # noqa: BLE001
            continue
        if rm.status_code != 200 or not rm.text.strip().startswith("{"):
            continue
        import json

        try:
            data = json.loads(rm.text)
        except ValueError:
            continue
        sources = [s for s in data.get("sources", []) if not s.startswith(("webpack://", "<"))][:40]
        return "\n".join(
            [
                f"SOURCE MAP EXPOSED: {c}",
                f"sources ({len(sources)} shown, {len(data.get('sources', []))} total):",
                *("  " + s.replace("../../", "").replace("../", "") for s in sources),
                "",
                "next: read original sources via the map's sourcesContent, or fetch "
                "individual files for the API/auth logic.",
            ]
        )
    return f"No source map exposed for {url} (checked: {', '.join(seen)})"
