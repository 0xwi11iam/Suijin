"""CISA KEV mirror — offline CVE intelligence with no API key.

`suijin pull cve` downloads the Known Exploited Vulnerabilities catalog
(a public JSON feed, no auth) into suijin/cve_cache/kev.json. search_cve
uses it as an offline fallback when NVD is unreachable, and it powers the
ACTIVELY EXPLOITED badge without NVD_API_KEY.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import requests


def _workspace_caches() -> Path:
    """The workspace caches dir (v4.1: runtime data lives in the agent
    workspace, not the package). Monkeypatch-friendly: tests may setattr
    DB_PATH / CACHE_DIR directly — this only feeds the DEFAULTS below."""
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    d = WORKSPACE_DIR / "caches"
    return d


CVE_CACHE_DIR = _workspace_caches() / "cve_cache"
KEV_PATH = CVE_CACHE_DIR / "kev.json"
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"


def pull_kev(force: bool = False, session=None, log=print) -> dict:
    """Fetch/refresh the KEV catalog. Returns {"count": n, "retrieved": iso}."""
    CVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if KEV_PATH.exists() and not force:
        try:
            data = json.loads(KEV_PATH.read_text())
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(data["retrieved"])).total_seconds() / 3600
            if age_h < 24:
                log(f"[cve] KEV cache fresh ({data['count']} CVEs, {age_h:.0f}h old) — use --force to refresh")
                return data
        except (ValueError, KeyError):
            pass  # corrupt — refetch
    req = session or requests.Session()
    resp = req.get(KEV_URL, timeout=60)
    resp.raise_for_status()
    catalog = resp.json()
    out = {
        "retrieved": datetime.now(timezone.utc).isoformat(),
        "count": catalog.get("count", len(catalog.get("vulnerabilities", []))),
        "vulnerabilities": catalog.get("vulnerabilities", []),
    }
    tmp = KEV_PATH.with_suffix(".part")
    tmp.write_text(json.dumps(out))
    tmp.replace(KEV_PATH)
    log(f"[cve] KEV catalog: {out['count']} actively-exploited CVEs -> {KEV_PATH.name}")
    return out


def load_kev() -> list[dict] | None:
    """Parsed vulnerabilities list, or None when no cache exists."""
    if not KEV_PATH.exists():
        return None
    try:
        return json.loads(KEV_PATH.read_text()).get("vulnerabilities", [])
    except ValueError:
        return None


def search_kev(software: str, version: str | None = None, limit: int = 5) -> list[dict]:
    """Match KEV entries by product/vendor/description keywords. Offline."""
    vulns = load_kev()
    if vulns is None:
        return []
    words = [w.lower() for w in software.split() if len(w) >= 3]
    if not words:
        return []
    scored = []
    for v in vulns:
        hay = " ".join(
            [
                v.get("product", ""),
                v.get("vendorProject", ""),
                v.get("vulnerabilityName", ""),
                v.get("description", ""),
            ]
        ).lower()
        hits = sum(1 for w in words if w in hay)
        if hits == 0:
            continue
        exact = bool(version and version in (v.get("product", "") + " " + v.get("vulnerabilityName", "")))
        scored.append((hits + (100 if exact else 0), v))
    scored.sort(key=lambda x: -x[0])
    return [v for _, v in scored[:limit]]


def format_kev_results(vulns: list[dict], software: str) -> str:
    lines = [f"[KEV offline] {len(vulns)} actively-exploited CVE(s) for '{software}':"]
    for v in vulns:
        lines.append(
            f"- {v.get('cveID', '?')} — {v.get('vulnerabilityName', '')[:100]}\n"
            f"    product: {v.get('product', '?')} | due: {v.get('dueDate', '?')} | "
            f"required action: {v.get('requiredAction', '')[:120]}"
        )
    lines.append("\n(NVD unreachable — results from the local CISA KEV mirror; refresh with: suijin pull cve)")
    return "\n".join(lines)


def kev_status() -> dict | None:
    if not KEV_PATH.exists():
        return None
    try:
        data = json.loads(KEV_PATH.read_text())
        return {"count": data.get("count", 0), "retrieved": data.get("retrieved", "?")}
    except ValueError:
        return None
