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
    hdr = _headers()
    hdr["Cookie"] = f"_crowdcontrol_session={token}"
    out = []
    url = "https://bugcrowd.com/api/programs.json?page=1"
    pages = 0
    while url and pages < _MAX_PAGES:
        data = _get(url, hdr).json()
        progs = data if isinstance(data, list) else data.get("programs", data.get("data", []))
        for prog in progs:
            name = prog.get("name") or prog.get("code") or prog.get("slug", "")
            if want and name not in want:
                continue
            # Bugcrowd embeds scope in the program object
            for scope in prog.get("taxonomy_terms", []) or prog.get("in_scope", []) or []:
                if isinstance(scope, dict):
                    out.append(
                        {
                            "program": name,
                            "asset": scope.get("name") or scope.get("target", ""),
                            "type": scope.get("category") or scope.get("type", ""),
                            "eligible": scope.get("eligible_for_bounty", True),
                            "instruction": "",
                            "source": f"https://bugcrowd.com/{prog.get('code', prog.get('slug', name))}",
                        }
                    )
        nxt = data.get("next") if isinstance(data, dict) else None
        url = f"https://bugcrowd.com/api/programs.json?page={pages + 2}" if nxt else None
        pages += 1
    return out


def _pull_ywh(token: str, want: set[str] | None) -> list[dict]:
    hdr = _headers(token)
    out = []
    url = "https://api.yeswehack.com/programs?page=1&size=100"
    pages = 0
    while url and pages < _MAX_PAGES:
        data = _get(url, hdr).json()
        for prog in data.get("items", []):
            slug = prog.get("slug", "")
            if want and slug not in want:
                continue
            for s in prog.get("scope", []):
                out.append(
                    {
                        "program": slug,
                        "asset": s.get("target", ""),
                        "type": s.get("scope_type", ""),
                        "eligible": s.get("eligible", True),
                        "instruction": "",
                        "source": f"https://yeswehack.com/programs/{slug}",
                    }
                )
        page = data.get("pagination", {})
        url = page.get("next_page_url") if page.get("next") else None
        pages += 1
    return out


def _pull_intigriti(token: str, want: set[str] | None) -> list[dict]:
    hdr = _headers(token)
    out = []
    url = "https://api.intigriti.com/core/v1/programs?take=100&skip=0"
    pages = 0
    while url and pages < _MAX_PAGES:
        data = _get(url, hdr).json()
        records = data.get("records", []) if isinstance(data, dict) else data
        for prog in records:
            handle = prog.get("handle", "")
            if want and handle not in want:
                continue
            for dom in prog.get("domains", []):
                for content in dom.get("content", []) or [{}]:
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
        if isinstance(data, dict) and len(records) == 100:
            skip = pages * 100
            url = f"https://api.intigriti.com/core/v1/programs?take=100&skip={skip}"
        else:
            url = None
        pages += 1
    return out


def _pull_immunefi(token: str, want: set[str] | None) -> list[dict]:
    hdr = _headers(token)
    out = []
    data = _get("https://immunefi.com/api/bounties.json", hdr).json()
    for prog in data if isinstance(data, list) else [data]:
        name = prog.get("name", "")
        if want and name not in want:
            continue
        for impact in prog.get("impact", {}).values():
            for target in impact.get("targets", []) if isinstance(impact, dict) else []:
                if isinstance(target, dict):
                    out.append(
                        {
                            "program": name,
                            "asset": target.get("target", ""),
                            "type": "blockchain" if target.get("target", "").startswith("0x") else "web",
                            "eligible": True,
                            "instruction": "",
                            "source": "https://immunefi.com/bugbounty/",
                        }
                    )
    return out


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
