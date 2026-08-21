"""Bug-bounty scope scraper — the bbscope method, in Suijin.

Platform APIs, auth, pagination, and scope shapes follow the bbscope
reference (references/bbscope): programs list -> per-program structured
scope -> normalize to {program, asset, type, eligibility, source_url}.

Differences from bbscope (deliberate):
  - SSL ALWAYS verified (bbscope ships InsecureSkipVerify:true — we do
    not bring that bug forward; every host here is a real API with
    valid certs)
  - stealth identity UA (no go-http-client/ scanner signature)
  - burst-limited pacing from platform stealth

Credentials: passed by the operator per call (never written to disk,
never logged, only ever sent to the platform's own API host).
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

_TIMEOUT = (8, 30)
_MAX_PAGES = 50  # runaway-pagination guard


def _headers(token: str | None = None) -> dict:
    try:
        from suijin.modules.platform.lib.stealth import user_agent

        ua = user_agent()
    except Exception:  # noqa: BLE001 — standalone fallback
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    h = {"User-Agent": ua, "Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(url: str, headers: dict, params: dict | None = None) -> requests.Response:
    """SSL-verified GET with burst-limited pacing."""
    try:
        from suijin.modules.platform.lib.stealth import pace

        pace()
    except Exception:  # noqa: BLE001
        pass
    r = requests.get(url, headers=headers, params=params, timeout=_TIMEOUT, verify=True)
    r.raise_for_status()
    return r


def _store() -> Path:
    from suijin.modules.platform.lib.workspace import artifact_dir

    d = artifact_dir("bugscope")
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── platform adapters ─────────────────────────────────────────────────────


def _pull_h1(token: str, want: set[str] | None) -> list[dict]:
    """HackerOne: basic auth user:token; JSON:API pagination via links.next."""
    import base64

    b64 = base64.b64encode(token.encode()).decode()
    hdr = _headers()
    hdr["Authorization"] = f"Basic {b64}"
    out = []
    url = "https://api.hackerone.com/v1/hackers/programs?page[size]=100"
    pages = 0
    while url and pages < _MAX_PAGES:
        data = _get(url, hdr).json()
        for prog in data.get("data", []):
            handle = prog.get("attributes", {}).get("handle", "")
            if want and handle not in want:
                continue
            out += _h1_scope(handle, hdr)
        url = (data.get("links") or {}).get("next")
        pages += 1
    return out


def _h1_scope(handle: str, hdr: dict) -> list[dict]:
    out = []
    url = f"https://api.hackerone.com/v1/hackers/programs/{handle}/structured_scopes?page[number]=1&page[size]=100"
    pages = 0
    while url and pages < _MAX_PAGES:
        try:
            data = _get(url, hdr).json()
        except Exception:  # noqa: BLE001 — one program failing is not the batch
            return out
        for s in data.get("data", []):
            a = s.get("attributes", {})
            out.append(
                {
                    "program": handle,
                    "asset": a.get("asset_identifier", ""),
                    "type": a.get("asset_type", ""),
                    "eligible": a.get("eligible_for_bounty", False),
                    "instruction": str(a.get("instruction", ""))[:120],
                    "source": f"https://hackerone.com/{handle}",
                }
            )
        url = (data.get("links") or {}).get("next")
        pages += 1
    return out


def _pull_bugcrowd(token: str, want: set[str] | None) -> list[dict]:
    """Bugcrowd: engagements.json list (paginationMeta.totalCount), then
    per-program scope from the brief's version document (data.scope[].
    targets[] with inScope). Reference-accurate; WAF 403/406 surfaces
    as a clear error."""
    hdr = _headers()
    hdr["Cookie"] = f"_crowdcontrol_session={token}"
    handles = []
    page = 1
    total = 0
    while page <= _MAX_PAGES:
        data = _get(
            f"https://bugcrowd.com/engagements.json?category=&sort_by=promoted&sort_direction=desc&page={page}", hdr
        ).json()
        engagements = data.get("engagements", [])
        if not engagements:
            break
        total = total or int(data.get("paginationMeta", {}).get("totalCount", 0))
        for e in engagements:
            url_path = (e.get("briefUrl") or "").strip()
            if url_path:
                handles.append(url_path)
        if total and len(handles) >= total:
            break
        page += 1
    out = []
    for path in handles:
        name = path.strip("/").split("/")[-1]
        if want and name not in want:
            continue
        try:
            out += _bugcrowd_scope(path, hdr)
        except Exception:  # noqa: BLE001 — skip failing programs
            continue
    return out


def _bugcrowd_scope(path: str, hdr: dict) -> list[dict]:
    """Per-program scope: fetch the page, locate the brief's version
    document API endpoint, read data.scope[].targets[]."""
    import re as _re

    page = _get(f"https://bugcrowd.com{path}" if path.startswith("/") else path, hdr)
    m = _re.search(r'"engagementBriefApi":\{[^}]*"getBriefVersionDocument":"([^"]+)"', page.text)
    if not m:
        return []
    doc_url = m.group(1).replace("\\/", "/")
    detail = _get(f"https://bugcrowd.com{doc_url}" if doc_url.startswith("/") else doc_url, hdr).json()
    out = []
    for group in detail.get("data", {}).get("scope", []):
        if not isinstance(group, dict):
            continue
        in_scope = bool(group.get("inScope"))
        for t in group.get("targets", []):
            if not isinstance(t, dict):
                continue
            asset = t.get("uri") or t.get("name", "")
            out.append(
                {
                    "program": path.strip("/").split("/")[-1],
                    "asset": asset,
                    "type": t.get("category", ""),
                    "eligible": in_scope,
                    "instruction": str(t.get("description", ""))[:120],
                    "source": f"https://bugcrowd.com{path}",
                }
            )
    return out


def _pull_ywh(token: str, want: set[str] | None) -> list[dict]:
    """YesWeHack: list program slugs, then per-program detail for scopes.

    Reference shape: detail has scopes[] ({scope, scope_type, ...}) and
    out_of_scope[]; the list endpoint only carries the slug."""
    hdr = _headers(token)
    slugs = []
    url = "https://api.yeswehack.com/programs?page=1"
    pages = 0
    while url and pages < _MAX_PAGES:
        data = _get(url, hdr).json()
        for prog in data.get("items", []):
            slug = prog.get("slug", "")
            if slug and (want is None or slug in want):
                slugs.append(slug)
        page = data.get("pagination", {})
        url = page.get("next_page_url") if page.get("next") else None
        pages += 1
    out = []
    for slug in slugs:
        try:
            detail = _get(f"https://api.yeswehack.com/programs/{slug}", hdr).json()
        except Exception:  # noqa: BLE001 — one program failing is not the batch
            continue
        oos = {o.get("scope", "") for o in detail.get("out_of_scope", []) if isinstance(o, dict)}
        for sc in detail.get("scopes", []):
            if not isinstance(sc, dict):
                continue
            asset = sc.get("scope", "")
            out.append(
                {
                    "program": slug,
                    "asset": asset,
                    "type": sc.get("scope_type", ""),
                    "eligible": asset not in oos and sc.get("enabled", True),
                    "instruction": "",
                    "source": f"https://yeswehack.com/programs/{slug}",
                }
            )
    return out


def _pull_intigriti(token: str, want: set[str] | None) -> list[dict]:
    """Intigriti: external/researcher/v1, offset accumulates by page size
    until >= total. Scope = domains.content[].endpoint."""
    hdr = _headers(token)
    out = []
    offset = 0
    total = None
    pages = 0
    while pages < _MAX_PAGES:
        data = _get(f"https://api.intigriti.com/external/researcher/v1/programs?limit=100&offset={offset}", hdr).json()
        if total is None and isinstance(data, dict):
            total = int(data.get("total") or data.get("totalCount") or 0)
        records = data.get("records", []) if isinstance(data, dict) else data
        if not records:
            break
        for prog in records:
            handle = prog.get("handle", "")
            if want and handle not in want:
                continue
            for dom in prog.get("domains", []):
                if not isinstance(dom, dict):
                    continue
                for content in dom.get("content", []) or []:
                    if not isinstance(content, dict):
                        continue
                    out.append(
                        {
                            "program": handle,
                            "asset": content.get("endpoint", dom.get("endpoint", "")),
                            "type": dom.get("type", ""),
                            "eligible": bool(dom.get("tier", 1)),
                            "instruction": "",
                            "source": f"https://app.intigriti.com/programs/{handle}",
                        }
                    )
        offset += len(records)
        if total and offset >= total:
            break
        pages += 1
    return out


def _pull_immunefi(token: str, want: set[str] | None) -> list[dict]:
    """Immunefi (unofficial, like bbscope): scrape the public bug-bounty
    page's RSC payload for the bounties list (slug per program), then
    each program page's embedded \"assets\":[...] array ({url, type,
    description, category}). Bearer token kept for authenticated pulls
    if the page requires one."""
    hdr = _headers(token)
    import re as _re

    page = _get("https://immunefi.com/bug-bounty/", hdr)
    m = _re.search(r'"bounties":\[', page.text)
    slugs = []
    if m:
        blob = _extract_json_array(page.text[m.end() - 1 :])
        try:
            for prog in json.loads(blob):
                slug = prog.get("slug", "")
                if slug and not prog.get("inviteOnly", False):
                    slugs.append(slug)
        except Exception:  # noqa: BLE001 — RSC payload drift is data, not fatal
            pass
    out = []
    for slug in slugs:
        if want and slug not in want:
            continue
        try:
            prog_page = _get(f"https://immunefi.com/bug-bounty/{slug}/information/", hdr)
        except Exception:  # noqa: BLE001
            continue
        am = _re.search(r'"assets":\[', prog_page.text)
        if not am:
            continue
        ablob = _extract_json_array(prog_page.text[am.end() - 1 :])
        try:
            for asset in json.loads(ablob):
                if not isinstance(asset, dict):
                    continue
                url_a = asset.get("url", "")
                out.append(
                    {
                        "program": slug,
                        "asset": url_a,
                        "type": asset.get("category") or asset.get("type", ""),
                        "eligible": True,
                        "instruction": str(asset.get("description", ""))[:120],
                        "source": f"https://immunefi.com/bug-bounty/{slug}/",
                    }
                )
        except Exception:  # noqa: BLE001
            continue
    return out


def _extract_json_array(text_from_bracket: str) -> str:
    """Return the first complete JSON array starting at text[0]=='['.
    Balanced-bracket scan, string-aware (RSC payloads embed JSON in JS)."""
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(text_from_bracket):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text_from_bracket[: i + 1]
    return ""


_PLATFORMS = {
    "h1": _pull_h1,
    "bugcrowd": _pull_bugcrowd,
    "ywh": _pull_ywh,
    "intigriti": _pull_intigriti,
    "immunefi": _pull_immunefi,
}


def scope_pull(platform: str = "", token: str = "", programs: str = "") -> str:
    """Pull program scopes from a bug-bounty platform."""
    plat = (platform or "").lower().strip()
    if plat not in _PLATFORMS:
        return f"Error: platform must be one of {sorted(_PLATFORMS)}"
    if not token:
        return "Error: token required (h1: 'username:api_token'; others: the bearer/session token from the operator)"
    want = {p.strip() for p in programs.split(",") if p.strip()} or None
    try:
        rows = _PLATFORMS[plat](token, want)
    except requests.exceptions.SSLError as e:
        return f"Error: SSL verification failed ({e}) — the platform cert chain could not be verified; refusing insecure fallback"
    except requests.exceptions.HTTPError as e:
        status = getattr(e.response, "status_code", "?")
        hint = "check the token" if status in (401, 403) else "the platform API may have changed"
        return f"Error: HTTP {status} from {plat} ({hint})"
    except requests.RequestException as e:
        return f"Error: {type(e).__name__}: {e}"
    out = _store() / f"{plat}.json"
    out.write_text(json.dumps(rows, indent=2))
    uniq_prog = {r["program"] for r in rows}
    return (
        f"pulled {len(rows)} scope entries across {len(uniq_prog)} program(s) -> {out}\n"
        f"query with scope_search (offline). sample: "
        + (f"{rows[0]['asset'][:50]}" if rows else "(no public programs matched)")
    )


def scope_search(keyword: str = "", in_scope: str = "true") -> str:
    """Search already-pulled scopes by keyword (offline)."""
    if not keyword:
        return "Error: keyword required"
    only_in = (in_scope or "true").lower().startswith("t")
    hits = []
    for p in sorted(_store().glob("*.json")):
        try:
            rows = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        for r in rows:
            if keyword.lower() in r.get("asset", "").lower():
                if only_in and not r.get("eligible", True):
                    continue
                hits.append(f"[{p.stem}:{r['program']}] {r['asset']} ({r.get('type', '?')})")
    if not hits:
        return f"No {'in-scope ' if only_in else ''}assets matching {keyword!r} (pull scopes first with scope_pull)"
    return f"{len(hits)} match(es):\n  " + "\n  ".join(hits[:40])
