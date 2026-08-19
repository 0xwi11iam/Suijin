"""KB-powered agent tools — all offline, no API keys.

find_wordlist    materialize SecLists wordlists from the cached tarball
kb_stats         knowledge-base inventory for the agent
suggest_exploit  service fingerprint -> GTFOBins + HackTricks lookups
extract_payloads pull code blocks out of KB docs into suijin_agent/payloads/
wordlist_tool    merge / dedupe / length-filter wordlists
mine_failures    cluster failure_db.json patterns so the agent stops repeating them
anonymize_report scrub IPs / emails / tokens / keys before sharing a report
"""

from __future__ import annotations

import json
import re
import sqlite3
import tarfile
from difflib import SequenceMatcher
from pathlib import Path

from suijin.modules.knowledge.lib.kb import CACHE_DIR, DB_PATH, SOURCES


def _ws():
    """platform workspace accessors, resolved lazily (boundary rule: no
    module-level cross-module imports — knowledge requires platform via
    its manifest, not via import-time welding)."""
    from suijin.modules.platform.lib import workspace

    return workspace


# ── find_wordlist ──────────────────────────────────────────────────────


def find_wordlist(keyword: str, extract: bool = True) -> str:
    """Find SecLists wordlists by keyword and optionally materialize them.

    Searches the KB index for seclists paths matching the keyword; with
    extract=True the full file is pulled out of the cached tarball into
    suijin_agent/wordlists/ (the DB copy may be truncated for indexing).
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return "Error: keyword required (e.g. 'directory', 'password', 'usernames', 'api')."
    if not DB_PATH.exists():
        return "Knowledge base DISABLED. Run 'suijin pull kb --sources seclists' to get the wordlists, then retry."
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            q = f"%{keyword}%"
            rows = conn.execute(
                "SELECT path, length(content) FROM kb_files "
                "WHERE source='seclists' AND (path LIKE ? OR title LIKE ?) LIMIT 10",
                (q, q),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        return f"KB Error: {e}"

    if not rows:
        return (
            f"No seclists wordlists match '{keyword}'. The source may not be "
            "pulled — run 'suijin pull kb --sources seclists' (~300 MB)."
        )

    lines = [f"SecLists matches for '{keyword}':"]
    extracted = []
    for path, size in rows:
        note = f"  - {path} ({size // 1024} KB indexed)"
        if extract:
            out = _extract_seclists_file(path)
            note += f" -> {out}" if out else " -> extraction failed"
            if out:
                extracted.append(out)
        lines.append(note)
    if extracted:
        lines.append("\nUse directly: ffuf -w suijin_agent/wordlists/<file> ...")
    else:
        lines.append("\nPass extract=true (default) to materialize files into suijin_agent/wordlists/.")
    return "\n".join(lines)


def _extract_seclists_file(rel_path: str) -> str | None:
    """Pull one file out of the cached seclists tarball into the workspace."""
    tar_path = CACHE_DIR / "seclists.tar.gz"
    if not tar_path.exists():
        return None
    base = Path(rel_path).name
    try:
        with tarfile.open(tar_path, mode="r:gz") as tar:
            member = next(
                (m for m in tar.getmembers() if m.isfile() and m.name.endswith("/" + rel_path)),
                None,
            )
            if member is None:
                return None
            data = tar.extractfile(member).read()
        out_dir = _ws().resolve_workspace_path("wordlists")
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / base
        out.write_bytes(data)
        return str(out.relative_to(_ws().WORKSPACE_DIR))
    except (tarfile.TarError, OSError):
        return None


# ── kb_stats ───────────────────────────────────────────────────────────


def kb_stats() -> str:
    """Inventory of the local knowledge base: per-source counts, age, health."""
    if not DB_PATH.exists():
        return "Knowledge base DISABLED — ask the operator to run 'suijin pull kb'. Use web_search meanwhile."
    from suijin.modules.knowledge.lib.kb import kb_status

    st = kb_status()
    if not st:
        return "Knowledge base DB present but unreadable — rebuild with 'suijin pull kb --force'."
    lines = [
        f"Knowledge base: {st['docs']:,} docs / {st['sources']} sources "
        f"(built {st['built_at'][:10]}"
        + (f", {st['age_days']}d old" if st.get("age_days") is not None else "")
        + f", {'FTS5' if st.get('fts5') else 'LIKE'})",
        "Sources (query with source:<name>):",
    ]
    repos = {name: cfg["repo"] for name, cfg in SOURCES.items()}
    for name, count in sorted(st.get("per_source", {}).items()):
        lines.append(f"  {name:12} {count:>6,} docs  ({repos.get(name, '?')})")
    for name in sorted(st.get("failed", {})):
        lines.append(f"  {name:12} FAILED at last build — retry: suijin pull kb --sources {name}")
    lines.append('\nSearch: search_kb {"keyword": "...", "limit": 5}; phrases: \'"union select"\'.')
    return "\n".join(lines)


# ── suggest_exploit ────────────────────────────────────────────────────


def suggest_exploit(service: str, version: str = "") -> str:
    """Offline exploit suggestions for a fingerprinted service.

    Chains three local lookups: GTFOBins page for the binary name (privesc),
    HackTricks pages for the service, PayloadsAllTheThings payloads.
    CVEs stay online — follow up with search_cve for exact-version matches.
    """
    service = (service or "").strip().lower()
    if not service:
        return "Error: service required (e.g. 'awk', 'apache httpd', 'mysql')."
    if not DB_PATH.exists():
        return (
            "Knowledge base DISABLED. Run 'suijin pull kb' first, or use web_search. "
            "Tell the operator in your final report."
        )
    binname = service.split()[0]
    out = [f"Offline suggestions for '{service}'" + (f" {version}" if version else "") + ":"]

    # 1. GTFOBins exact binary (privesc), with fuzzy fallback for near-misses
    fuzzy_note = ""
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT path, substr(content, 1, 600) FROM kb_files WHERE source='gtfobins' AND path = ? LIMIT 1",
                (f"_gtfobins/{binname}",),
            ).fetchone()
            if row is None:
                from difflib import get_close_matches

                names = [
                    r[0].rsplit("/", 1)[-1] for r in conn.execute("SELECT path FROM kb_files WHERE source='gtfobins'")
                ]
                close = get_close_matches(binname, names, n=1, cutoff=0.75)
                if close:
                    row = conn.execute(
                        "SELECT path, substr(content, 1, 600) FROM kb_files WHERE source='gtfobins' AND path = ? LIMIT 1",
                        (f"_gtfobins/{close[0]}",),
                    ).fetchone()
                    if row:
                        fuzzy_note = f" (fuzzy: {binname} ~ {close[0]})"
        finally:
            conn.close()
    except sqlite3.Error:
        row = None
    if row:
        out.append(f"\n[gtfobins] {binname} is a living-off-the-land binary (path: {row[0]}){fuzzy_note}:")
        out.append("  " + row[1].replace("\n", "\n  ")[:600])

    # 2/3. HackTricks + PayloadsAllTheThings FTS hits via search_kb
    from suijin.modules.tools.lib.intel import search_kb  # tools module (slice 5)

    for source, label in (("hacktricks", "hacktricks"), ("payloads", "payloads")):
        res = search_kb(f"source:{source} {service}", limit=3)
        if not res.startswith("No matching") and "DISABLED" not in res and "no docs" not in res:
            out.append(f"\n[{label}]")
            out.append(res.rstrip())

    out.append("\nNext: search_cve for exact-version CVEs; verify before exploiting.")
    return "\n".join(out)


# ── extract_payloads ───────────────────────────────────────────────────

_CODE_BLOCK_RE = re.compile(r"```([A-Za-z0-9_+-]*)\n(.*?)```", re.DOTALL)
_EXT_MAP = {
    "python": ".py",
    "py": ".py",
    "bash": ".sh",
    "sh": ".sh",
    "shell": ".sh",
    "sql": ".sql",
    "javascript": ".js",
    "js": ".js",
    "json": ".json",
    "html": ".html",
    "xml": ".xml",
    "yaml": ".yml",
    "yml": ".yml",
    "powershell": ".ps1",
    "ps1": ".ps1",
    "php": ".php",
}


def extract_payloads(keyword: str, max_payloads: int = 10) -> str:
    """Pull runnable code blocks from matching KB docs into suijin_agent/payloads/."""
    keyword = (keyword or "").strip()
    if not keyword:
        return "Error: keyword required (e.g. 'reverse shell bash', 'sqli union bypass')."
    if not DB_PATH.exists():
        return "Knowledge base DISABLED. Run 'suijin pull kb' first."
    from suijin.modules.tools.lib.intel import search_kb  # tools module (slice 5)

    res = search_kb(keyword, limit=5)
    if res.startswith("No matching"):
        return res

    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            terms = [t.strip('"') for t in keyword.split() if t.strip('"')]
            like = f"%{terms[0]}%" if terms else "%"
            docs = conn.execute(
                "SELECT source, path, content FROM kb_files WHERE content LIKE ? LIMIT 5", (like,)
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        return f"KB Error: {e}"

    out_dir = _ws().resolve_workspace_path("payloads")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for source, path, content in docs:
        for lang, code in _CODE_BLOCK_RE.findall(content):
            code = code.strip()
            if not (8 <= len(code) <= 16_000):
                continue
            stem = Path(path).stem.replace("/", "_")[:40] or "kb"
            ext = _EXT_MAP.get(lang.lower(), ".txt")
            name = f"kb_{source}_{stem}{ext}"
            (out_dir / name).write_text(code + "\n")
            written.append(f"payloads/{name}")
            if len(written) >= max_payloads:
                break
        if len(written) >= max_payloads:
            break
    if not written:
        return (
            f"Matching KB docs for '{keyword}' contain no extractable code blocks "
            "(or all were outside the 8-16k size window). Read them with search_kb instead."
        )
    return "Extracted payload files (review before running):\n  " + "\n  ".join(written)


# ── wordlist_tool ──────────────────────────────────────────────────────


def wordlist_tool(action: str, files: list | None = None, out: str = "", min_len: int = 1, max_len: int = 256) -> str:
    """Merge / dedupe / length-filter wordlists into suijin_agent/wordlists/.

    actions: "dedupe" (keep first occurrence, preserve order),
             "merge" (concatenate + dedupe), "filter" (length window only).
    """
    action = (action or "").strip().lower()
    files = [str(f) for f in (files or []) if str(f).strip()]
    if action not in ("dedupe", "merge", "filter"):
        return "Error: action must be dedupe, merge, or filter."
    if not files:
        return "Error: files required — paths inside suijin_agent/ (e.g. wordlists/a.txt)."
    if not out:
        out = "wordlists/merged_" + action + ".txt"
    min_len, max_len = sorted((max(1, int(min_len)), max(1, int(max_len))))

    words: list[str] = []
    for f in files:
        try:
            p = _ws().resolve_workspace_path(f)
            words.extend(w for w in p.read_text(errors="ignore").splitlines() if w)
        except (OSError, PermissionError) as e:
            return f"Error reading '{f}': {e}"

    n_raw = len(words)
    if action in ("dedupe", "merge"):
        seen, deduped = set(), []
        for w in words:
            if w not in seen:
                seen.add(w)
                deduped.append(w)
        words = deduped
    words = [w for w in words if min_len <= len(w) <= max_len]

    target = _ws().resolve_workspace_path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(words) + "\n")
    return (
        f"{action}: {len(files)} file(s) -> {len(words):,} words "
        f"({n_raw - len(words):,} dropped) -> {target.relative_to(_ws().WORKSPACE_DIR)}"
    )


# ── mine_failures ──────────────────────────────────────────────────────


def mine_failures(max_clusters: int = 5) -> str:
    """Cluster failure_db.json entries so the agent stops repeating mistakes."""
    db = _ws().WORKSPACE_DIR / "failure_db.json"
    if not db.exists():
        return "No failure history yet (suijin_agent/failure_db.json). Record failures with record_finding as you go."
    try:
        entries = json.loads(db.read_text())
    except ValueError:
        return "failure_db.json is corrupted — delete it to start fresh."
    if not entries:
        return "Failure DB is empty."

    clusters: list[list[dict]] = []
    for e in entries:
        sig = f"{e.get('technique', '')} :: {e.get('reason', '')}"
        for c in clusters:
            if SequenceMatcher(None, sig, c[0]["_sig"]).ratio() > 0.6:
                c.append(e)
                break
        else:
            e["_sig"] = sig
            clusters.append([e])
    clusters.sort(key=len, reverse=True)

    lines = [f"Failure patterns ({len(entries)} entries -> {len(clusters)} clusters):"]
    for c in clusters[:max_clusters]:
        targets = {e.get("target", "?") for e in c}
        seen = sum(e.get("times_seen", 1) for e in c)
        lines.append(
            f"  [{len(c)}x / {seen} seen] {c[0].get('technique', '?')} — "
            f"{c[0].get('reason', '?')[:120]} (targets: {', '.join(sorted(targets)[:3])})"
        )
    if len(clusters) > max_clusters:
        lines.append(f"  ... {len(clusters) - max_clusters} smaller clusters")
    lines.append("\nAVOID these technique/reason combos on the listed targets.")
    return "\n".join(lines)


# ── anonymize_report ───────────────────────────────────────────────────

_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]{8,}")
_KEY_RE = re.compile(r"\b(?:sk|pk|api[_-]?key|dk_[A-Za-z0-9])[-_][A-Za-z0-9_-]{8,}\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")
_PRIVKEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)
_SAFE_IPS = {"127.0.0.1", "0.0.0.0", "255.255.255.255"}


def anonymize_report(file_path: str) -> str:
    """Scrub identifiers (IPs, emails, tokens, keys, JWTs) from a report file.

    Writes the cleaned copy to suijin_agent/reports/anonymized/<name>.
    Flags (FLAG{...}) and localhost are preserved. Returns the output path
    plus a replacement count.
    """
    src = _ws().resolve_workspace_path(file_path)
    try:
        text = src.read_text(errors="ignore")
    except (OSError, PermissionError) as e:
        return f"Error reading '{file_path}': {e}"

    counters = {"ip": 0, "email": 0, "token": 0, "key": 0, "jwt": 0, "privkey": 0}

    def _sub_ip(m):
        if m.group(0) in _SAFE_IPS:
            return m.group(0)
        counters["ip"] += 1
        return f"[IP-{counters['ip']}]"

    def _sub_email(_m):
        counters["email"] += 1
        return f"[EMAIL-{counters['email']}]"

    def _sub_bearer(m):
        counters["token"] += 1
        return m.group(1) + f"[TOKEN-{counters['token']}]"

    def _sub_key(_m):
        counters["key"] += 1
        return f"[KEY-{counters['key']}]"

    def _sub_jwt(_m):
        counters["jwt"] += 1
        return f"[JWT-{counters['jwt']}]"

    def _sub_privkey(_m):
        counters["privkey"] += 1
        return "[PRIVATE KEY REDACTED]"

    text = _PRIVKEY_RE.sub(_sub_privkey, text)
    text = _JWT_RE.sub(_sub_jwt, text)
    text = _BEARER_RE.sub(_sub_bearer, text)
    text = _KEY_RE.sub(_sub_key, text)
    text = _EMAIL_RE.sub(_sub_email, text)
    text = _IP_RE.sub(_sub_ip, text)

    out_dir = _ws().resolve_workspace_path("reports") / "anonymized"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / src.name
    out.write_text(text)
    total = sum(counters.values())
    return f"Anonymized {total} item(s) {counters} -> {out.relative_to(_ws().WORKSPACE_DIR)}"
