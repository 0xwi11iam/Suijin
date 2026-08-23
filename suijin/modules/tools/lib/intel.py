"""Intelligence tools: NVD CVE search, knowledge base, knowledge graph, notes."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

import requests

DB_PATH = None  # resolved lazily from the knowledge KB; patchable via setattr


def _db_path():
    """KB database path (honours a monkeypatched module attr)."""
    v = globals().get("DB_PATH")
    if v is not None:
        return v
    from suijin.modules.knowledge.lib.kb import DB_PATH as _p

    return _p


def __getattr__(name):
    if name == "DB_PATH":
        return _db_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


BASE_DIR = Path(__file__).resolve().parents[3]  # suijin/ package

# ── NVD CVE search (NIST National Vulnerability Database) ────────────
NVD_BASE = "https://services.nvd.nist.gov/rest/json/cves/2.0"


def search_cve(software, config, version=None, limit=5):
    """Search NIST NVD for CVEs matching a software product/version."""
    if not software or not software.strip():
        return "Error: search_cve requires a 'software' argument."

    limit = max(1, min(int(limit or 5), 20))
    software = software.strip()
    version_str = version.strip() if version else None

    api_key = os.environ.get("NVD_API_KEY", "").strip()
    headers = {"User-Agent": "Suijin/1.0"}

    def _do_nvd(params):
        try:
            resp = requests.get(NVD_BASE, params=params, headers=headers, timeout=30)
            if resp.status_code != 200:
                return {"vulnerabilities": [], "totalResults": 0, "_error": f"NVD API returned HTTP {resp.status_code}"}
            return resp.json()
        except requests.exceptions.Timeout:
            return {"vulnerabilities": [], "totalResults": 0, "_error": "NVD API request timed out (30s)."}
        except requests.exceptions.ConnectionError:
            return {
                "vulnerabilities": [],
                "totalResults": 0,
                "_error": "Cannot reach NVD (services.nvd.nist.gov). Check network.",
            }
        except Exception as e:
            return {"vulnerabilities": [], "totalResults": 0, "_error": f"NVD API request failed — {e}"}

    data = None
    error = None
    max_total = 0
    fetch_limit = min(limit * 5, 50)  # fetch more than needed for local sorting

    for strategy in range(3):
        params = {"resultsPerPage": fetch_limit}
        if api_key:
            params["apiKey"] = api_key

        if strategy == 0 and version_str:
            keywords = software.split()
            core = keywords[0] if len(keywords) == 1 else software
            params["keywordSearch"] = f"{core} {version_str}"
        elif strategy == 1:
            params["keywordSearch"] = software
        else:
            params["keywordSearch"] = " ".join(software.split()[:2])

        data = _do_nvd(params)
        if data.get("_error"):
            error = data["_error"]
            continue
        if data.get("totalResults", 0) > 0:
            error = None
            break
        max_total = max(max_total, data.get("totalResults", 0))

    if error and max_total == 0:
        # NVD unreachable and nothing cached from earlier strategies — fall
        # back to the local CISA KEV mirror (suijin pull cve) if present.
        from suijin.modules.knowledge.lib.cve_mirror import format_kev_results, search_kev

        kev_hits = search_kev(software, version_str, limit=limit)
        if kev_hits:
            return format_kev_results(kev_hits, f"{software} {version_str or ''}".strip())
        return error

    vulns = data.get("vulnerabilities", []) if data else []
    if not vulns:
        q = f"{software} {version_str}" if version_str else software
        return f"No CVEs found for: {q}"

    scored = []
    for item in vulns:
        cve = item.get("cve", {})
        score_val, _ = _extract_cvss(cve)
        try:
            score_float = float(score_val) if score_val != "N/A" else 0.0
        except (ValueError, TypeError):
            score_float = 0.0

        if version_str:
            descs = str(cve.get("descriptions", ""))
            if version_str in descs:
                score_float += 100  # exact version matches ranked first

        scored.append((score_float, item))

    scored.sort(key=lambda x: -x[0])
    vulns = [item for _, item in scored[:limit]]

    if version_str and strategy > 0:
        filtered = [v for v in vulns if version_str in str(v.get("cve", {}).get("descriptions", ""))]
        if filtered:
            vulns = filtered

    results = []
    for item in vulns:
        cve = item.get("cve", {})
        cve_id = cve.get("id", "UNKNOWN")

        desc_text = ""
        for d in cve.get("descriptions", []):
            if d.get("lang") == "en":
                desc_text = d.get("value", "")
                break

        score, severity = _extract_cvss(cve)
        kev = "ACTIVELY EXPLOITED" if _is_kev(cve) else ""

        refs = []
        for r in cve.get("references", []):
            url = r.get("url", "")
            source = r.get("source", "")
            for tag in r.get("tags") or []:
                tag_lower = tag.lower()
                if "exploit" in tag_lower or "patch" in tag_lower or "vendor" in tag_lower:
                    refs.append(f"  [{tag}] {url} ({source})")
                    break
        refs = refs[:3]

        weaknesses = []
        for w in cve.get("weaknesses", []):
            for wd in w.get("description", []):
                val = wd.get("value", "")
                if val and val != "NVD-CWE-noinfo" and val != "NVD-CWE-Other":
                    weaknesses.append(val)
        cwe_str = ", ".join(weaknesses[:3])

        entry = f"[{cve_id}] {severity} ({score})\n  {desc_text[:300]}\n"
        if cwe_str:
            entry += f"  CWE: {cwe_str}\n"
        if kev:
            entry += f"  {kev}\n"
        if refs:
            entry += "\n".join(refs) + "\n"

        results.append(entry)

    q = f"{software} {version_str}" if version_str else software
    total = len(results)
    header = f"Found {total} CVE(s) for '{q}':\n\n"
    return header + "\n".join(results)


def _extract_cvss(cve):
    """Extract the best CVSS score and severity label from a CVE object."""
    metrics = cve.get("metrics", {})

    for entry in metrics.get("cvssMetricV31", []):
        cvss = entry.get("cvssData", {})
        score = cvss.get("baseScore")
        sev = cvss.get("baseSeverity", "")
        if score is not None:
            return f"{score:.1f}", sev

    for entry in metrics.get("cvssMetricV30", []):
        cvss = entry.get("cvssData", {})
        score = cvss.get("baseScore")
        sev = cvss.get("baseSeverity", "")
        if score is not None:
            return f"{score:.1f}", sev

    for entry in metrics.get("cvssMetricV2", []):
        cvss = entry.get("cvssData", {})
        score = cvss.get("baseScore")
        if score is not None:
            return f"{score:.1f}", "MEDIUM"
    return "N/A", "UNKNOWN"


def _is_kev(cve):
    """Check if the CVE is in CISA's Known Exploited Vulnerabilities catalog."""
    kevs = cve.get("cisaExploitAdd") or cve.get("cisaActionDue")
    if kevs:
        return True
    vuln_status = cve.get("vulnStatus", "")
    return "Known Exploited" in str(vuln_status)


# ── Knowledge base & knowledge graph ─────────────────────────────────


def _fts_match_expr(keyword: str) -> str:
    """Turn free text into a safe FTS5 expression.

    Quoted spans become ordered PHRASE terms (FTS5 matches adjacent words in
    order): 'sql "union select" bypass' -> '"sql" "union select" "bypass"'.
    Unquoted words are quoted individually with implicit AND, as before.
    """
    exprs: list[str] = []
    pos = 0
    for m in re.finditer(r'"([^"]+)"', keyword):
        for word in keyword[pos : m.start()].replace('"', " ").split():
            exprs.append(f'"{word}"')
        phrase = " ".join(m.group(1).split())
        if phrase:
            exprs.append(f'"{phrase}"')
        pos = m.end()
    for word in keyword[pos:].replace('"', " ").split():
        exprs.append(f'"{word}"')
    return " ".join(exprs) or '""'


_SOURCE_FILTER_RE = re.compile(r"(?:^|\s)source:([A-Za-z0-9_-]+)")


def search_kb(keyword, limit=5):
    """Search the local knowledge base (FTS5 BM25, LIKE fallback).

    The KB is built by the operator via `suijin pull kb` — it contains
    HackTricks, PayloadsAllTheThings, GTFOBins, LOLBAS, OWASP cheat sheets
    and SecLists wordlists. Degrades gracefully if not built.

    - `source:<name>` in the keyword restricts the search to one KB source
      (e.g. "source:gtfobins awk sudo"); unknown sources are reported.
    - `limit` clamps to 1-20 results (default 5).
    """
    if not _db_path().exists():
        return (
            "Knowledge base DISABLED. The operator must run 'suijin pull kb' to download and "
            "index HackTricks, PayloadsAllTheThings, GTFOBins, LOLBAS, OWASP, SecLists. "
            "Tell them in your final report. Meanwhile use web_search, or check_knowledge "
            "for engagement-specific memory."
        )
    keyword = (keyword or "").strip()
    try:
        limit = max(1, min(int(limit or 5), 20))
    except (TypeError, ValueError):
        limit = 5

    source_filter = None
    m = _SOURCE_FILTER_RE.search(keyword)
    if m:
        source_filter = m.group(1)
        keyword = _SOURCE_FILTER_RE.sub(" ", keyword).strip()

    try:
        conn = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
        try:
            c = conn.cursor()
            if source_filter:
                avail = {r[0] for r in c.execute("SELECT DISTINCT source FROM kb_files")}
                if source_filter not in avail:
                    return (
                        f"KB source '{source_filter}' has no docs in this build "
                        f"(available: {', '.join(sorted(avail))})."
                    )
            rows = None
            # FTS5 ranked search (preferred)
            try:
                sql = (
                    "SELECT kb_files.source, kb_files.path, kb_files.title, "
                    "snippet(kb_fts, 2, '', '', ' … ', 18) "
                    "FROM kb_fts JOIN kb_files ON kb_files.id = kb_fts.rowid "
                    "WHERE kb_fts MATCH ?"
                )
                params: list = [_fts_match_expr(keyword)]
                if source_filter:
                    sql += " AND kb_files.source = ?"
                    params.append(source_filter)
                sql += " ORDER BY rank LIMIT ?"
                params.append(limit)
                c.execute(sql, params)
                rows = c.fetchall()
            except sqlite3.OperationalError:
                rows = None
            # LIKE fallback (FTS unavailable or query syntax rejected)
            if rows is None:
                q = f"%{keyword}%"
                sql = (
                    "SELECT source, path, title, substr(content, 1, 400) "
                    "FROM kb_files WHERE content LIKE ? OR path LIKE ? OR title LIKE ?"
                )
                params = [q, q, q]
                if source_filter:
                    sql += " AND source = ?"
                    params.append(source_filter)
                sql += " LIMIT ?"
                params.append(limit)
                c.execute(sql, params)
                rows = c.fetchall()
            if not rows:
                hint = f" (source: {source_filter})" if source_filter else ""
                return f"No matching knowledge base entries for '{keyword}'{hint}. Try broader terms or web_search."
            res = ""
            for source, path, title, snip in rows:
                res += f"--- [{source}] {title or path}\n    path: {path}\n    {snip}\n\n"
            from suijin.modules.platform.lib.runtime import truncate

            return truncate(res)
        finally:
            conn.close()
    except Exception as e:
        return f"KB Error: {str(e)}"


def check_knowledge(target, payload=None, config=None):
    """Query the knowledge graph for constraints on a target."""
    from suijin.modules.loader import load_local_module

    kg = load_local_module("knowledge_graph")

    if payload:
        result = kg.check_payload(target, payload)
        if result.get("blocked"):
            return f"BLOCKED: {result['reason']} (confidence: {result.get('confidence', 1.0):.0%})"
        return f"Payload not in any known blocked pattern for {target}."
    else:
        return kg.summary(target)


def record_finding(target, finding_type, rule, evidence="", config=None):
    """Record a finding — H5: claims are VERIFIED at claim time (an
    independent second check runs immediately; the verdict rides the
    result so the agent sees it on the next turn)."""
    from suijin.modules.loader import load_local_module

    kg = load_local_module("knowledge_graph")

    valid_types = ("blocks", "rate_limit", "waf", "verified_cve", "false_positive", "behavior", "bypass")
    if finding_type not in valid_types:
        return f"Invalid finding_type. Use one of: {', '.join(valid_types)}"

    kg.add_constraint(target, finding_type, rule, evidence=evidence or "", confidence=1.0)
    base = f"Recorded: {target} -> {finding_type} -> '{rule}'"

    # claim-time verification — never blocks the recording, only grades it
    try:
        from suijin.modules.agent.lib.verify import verify_finding

        v = verify_finding(
            {"type": finding_type, "target": target, "evidence": evidence or rule, "url": target},
            route_fn=lambda name, args, cfg: _safe_route(name, args),
        )
        verdict = v.get("verification", {}).get("verdict", "unverifiable")
        note = v.get("verification", {}).get("evidence", "")[:160]
        return f"{base}\nVerification: {verdict.upper()} — {note}"
    except Exception:  # noqa: BLE001 — grading must never break recording
        return base


def _safe_route(name, args):
    """Best-effort second-evidence dispatch; unknown tools grade as
    unverifiable rather than crashing the recording."""
    from suijin.modules.tools.lib.dispatch import route_tool

    return route_tool(name, args, {})


# ── Note-taking ──────────────────────────────────────────────────────
NOTES_DIR = BASE_DIR / ".notes"


def write_note(content, success=True, category="general", engagement=None, config=None):
    """Write a timestamped note to a per-engagement log file."""
    import datetime

    # Agent scratchpad hook (C2): the pad is re-injected on every
    # engagement's first turn — notes become persistent memory.
    try:
        from suijin.modules.agent.lib.scratchpad import append_note

        append_note(content, category=category)
    except Exception:  # noqa: BLE001 — never break note-taking
        pass

    NOTES_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    status = "SUCCESS" if success else "FAILED"

    if engagement:
        safe_name = re.sub(r"[^\w-]", "_", str(engagement).strip())[:48]
        filename = f"{safe_name}_notes.md"
    else:
        datestamp = now.strftime("%Y-%m-%d")
        filename = f"{datestamp}_notes.md"

    note_file = NOTES_DIR / filename

    header = f"\n---\n### {timestamp} — {status}\n**Category:** {category}\n\n"
    entry = header + content.strip() + "\n"

    if not note_file.exists():
        head = f"# Suijin Engagement Notes — {filename.replace('_notes.md', '')}\nStarted: {timestamp}\n---\n"
        note_file.write_text(head + entry, encoding="utf-8")
    else:
        with note_file.open("a", encoding="utf-8") as f:
            f.write(entry)

    return f"Note written to .notes/{filename} [{category}] - {status}"
