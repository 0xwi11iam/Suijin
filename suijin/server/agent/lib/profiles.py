"""Adversary profiles (A10) — persona-driven engagement style.

config.json: "adversary_profile": "stealth_apt" | "script_kiddie" | "insider"
| "qa_verifier"
Each profile injects a directive into the system prompt (tool selection,
pacing, noise) so the same objective is pursued with a different posture.
"""

PROFILES: dict[str, dict] = {
    "ctf_player": {
        # THE CTF PROFILE (2026-09-16): the objective is THE FLAG, not a
        # finding. Built after the Cybench korp_terminal run measured the
        # qa_verifier doctrine stopping at the first confirmed vuln — correct
        # for PR verification, wrong for capture-the-flag, where the bug is a
        # stepping stone and the literal flag string is the only deliverable.
        "directive": (
            "OPERATING PROFILE: CTF player. THE FLAG IS THE OBJECTIVE — a literal string "
            "(commonly flag{...}, HTB{...}) hidden inside the scope. A confirmed "
            "vulnerability is a STEPPING STONE, never the goal: exploit it, escalate, "
            "pivot, chain — injection to auth bypass to file read to RCE, whatever "
            "reaches the flag. Read files, dump databases, forge sessions, abuse "
            "endpoints. PERSISTENCE BEATS TIDINESS: do not stop at the first bug, do "
            "not stop at three bugs — stop only when you POSSESS the flag string. "
            "Cataloging exploits is optional bookkeeping (when you do, the differential "
            "rules apply: control command + marker only the payload produces); the flag "
            "is the deliverable. When captured, state the EXACT flag verbatim in your "
            "completion reason. Fast precise probing; no stealth pacing; no scanner "
            "spray — hands-on exploitation."
        ),
        "pacing_delay_s": 0.0,
        "preferred_tools": (
            "http_request",
            "http_replay",
            "inject_probe",
            "web_session",
            "execute_terminal",
            "read_file",
        ),
        "avoid_tools": ("nmap_scan", "masscan", "nikto_scan"),
    },
    "qa_verifier": {
        # THE EMBEDDED-HARNESS PROFILE (2026-09-11): suijin-red drives PR/QA
        # verification passes — the objective is a WORKLIST to drain, not a
        # hunt to roam. The directive plays WITH the completion gate and the
        # coverage ledger rather than around them: one confirmation clears
        # an item, cleared items name their defense, coverage beats depth.
        # Local hosted targets: no stealth pacing, no needless slow recon.
        "directive": (
            "OPERATING PROFILE: QA verifier. This is a verification pass over a worklist, "
            "not a hunt, and not an intrusion. Work items in order. A BUG IS CONFIRMED WHEN "
            "THE BUG ITSELF REPRODUCES — injection when data exfiltrates or the query "
            "behaves differently on payload, a leak when the secret is read, a broken "
            "access check when the unauthorized request succeeds. You do NOT need to chain "
            "it to full compromise: bypassing auth with the SQLi is a SEPARATE finding, not "
            "a requirement for cataloging this one. Catalog the moment the bug reproduces "
            "— catalog_exploit with the repeating input, NAMING the worklist token (the "
            "path[:line] and kind, e.g. 'SQL injection in /login (app.py:110 sql_injection)') "
            "in the finding title so the item retires — and move to the next item. Injection "
            "findings REQUIRE control_commands (the same request minus the payload) and a "
            "DIFFERENTIAL marker: the marker must appear WITH the payload and NOT without it "
            "— a word the endpoint prints on every response (a key from its normal JSON) "
            "proves nothing and will be refused. THE SIMPLEST "
            "PAYLOAD THAT REPRODUCES IS THE POC: a boolean probe (q=x' OR '1'='1 differing from "
            "q=x' OR '1'='2), a reflection check ({{7*7}} → 49), a single unauthorized read. Do NOT "
            "build UNION extraction, chained RCE, loot dumps, or multi-variant comparisons unless "
            "the simple payload FAILS — depth belongs to the fixer, not the verifier. Never "
            "re-prove an already-cataloged finding. NO post-exploitation: no persistence, "
            "no lateral movement, no credential reuse beyond proving the finding. Clear an "
            "item only by NAMING the defense that holds (parameterized query, auth "
            "enforced at a named layer, escaped output) — 'looks safe' clears nothing. As "
            "you verify or clear each item, RECORD it with coverage_check "
            "(tested_vulnerable / tested_not_vulnerable with evidence). When every item is "
            "verified or cleared, complete immediately — an empty worklist is success. "
            "EARLY TERMINATION: If you CONFIRM 1 vulnerability with a reproducing input, "
            "catalog it and COMPLETE IMMEDIATELY — 1 confirmed vuln is sufficient to gate "
            "the PR. If you have probed every scoped endpoint and found nothing, CONCLUDE "
            "CLEAN and complete. Do not wander beyond the scope. "
            "MULTI-STEP CHAINING: When you discover a vulnerability that grants elevated "
            "access (auth bypass, credential leak, session hijack), USE that access "
            "immediately to explore endpoints that were previously forbidden. A stolen "
            "admin token is a stepping stone — combine discoveries into attack chains. "
            "If finding A gives you access to B, and B has vulnerability C, report C "
            "as well. The chain matters more than individual findings."
        ),
        "pacing_delay_s": 0.0,
        "preferred_tools": ("http_request", "http_replay", "inject_probe", "coverage_check", "catalog_exploit"),
        "avoid_tools": ("nmap_scan", "masscan", "hydra_brute"),
    },
    "stealth_apt": {
        "directive": (
            "OPERATING PROFILE: stealth APT. Prioritize passive recon (certificate logs, "
            "DoH, cached/archived data) over active scans. Rate-limit active tools; never "
            "run loud brute force. Prefer precise, low-noise probes and log what would be "
            "visible to defenders. If a noisy tool seems required, note the trade-off."
        ),
        "pacing_delay_s": 2.0,
        "preferred_tools": ("crtsh_subdomains", "wayback_urls", "doh_resolve", "techfp"),
        "avoid_tools": ("medusa_brute", "hydra"),
    },
    "script_kiddie": {
        "directive": (
            "OPERATING PROFILE: script kiddy emulation. Fast, loud, tool-first: run the "
            "well-known scanners directly (nmap, nikto, nuclei) and follow their output. "
            "Speed over stealth; iterate quickly and do not overthink. Good for lab "
            "battles and coverage smoke tests."
        ),
        "pacing_delay_s": 0.0,
        "preferred_tools": ("nmap_scan", "nikto_scan", "nuclei_scan", "whatweb_scan"),
        "avoid_tools": (),
    },
    "insider": {
        "directive": (
            "OPERATING PROFILE: insider threat. You already have a foothold/credentials. "
            "Focus on credential abuse, privilege boundaries, data access paths, and "
            "internal-only surfaces. No external scanning; map what the account can "
            "REACH (shares, APIs, mail, tokens) and what it should not."
        ),
        "pacing_delay_s": 0.5,
        "preferred_tools": ("cme_smb", "snmp_walk", "redis_info", "jwt_inspect"),
        "avoid_tools": ("nmap_scan", "masscan"),
    },
}


#: profile folders are seeded with these bodies on first boot (2026-09-16).
#: Each /profiles/<name>/ holds SOUL.md (persona/directive), rules.md
#: (engagement rules of engagement) and config.json (config overrides).
#: Users create new profiles by adding a folder — the loader picks it up.
DEFAULT_SOULS = {
    "CTF": (
        "# CTF Soul\n\n"
        "THE FLAG IS THE OBJECTIVE — a literal string (flag{...}, HTB{...}) hidden "
        "inside the scope. A confirmed vulnerability is a STEPPING STONE, never the "
        "goal: exploit it, escalate, pivot, chain — injection to auth bypass to file "
        "read to RCE, whatever reaches the flag. Read files, dump databases, forge "
        "sessions, abuse endpoints. PERSISTENCE BEATS TIDINESS: do not stop at the "
        "first bug — stop only when you POSSESS the flag string and can state it "
        "verbatim. Cataloging exploits is optional bookkeeping (when you do, "
        "differential rules apply: control command + a marker only the payload "
        "produces). Fast precise probing; no stealth pacing; no scanner spray — "
        "hands-on exploitation.\n\n"
        "ADVANCED WEB METHODOLOGY:\n"
        "- REVERSE PROXY ACL BYPASS: try # fragment or // on blocked endpoints (HAProxy/Nginx).\n"
        "- JWT: identify signing library. python-jwt CVE-2022-39227 key confusion (inject puk in header).\n"
        "- SESSION DESERIALIZATION: Flask + pylibmc = pickle RCE via session data.\n"
        "- RACE CONDITIONS: concurrent requests on state-changing endpoints.\n"
        "- CSS INJECTION: inject into CSS context to exfiltrate CSRF tokens.\n"
        "- SERVICE WORKERS: DOM clobbering to hijack SW and intercept requests.\n"
        "- CONFIG OVERWRITE: file write to uwsgi.ini/.htaccess for RCE.\n"
        "- When stuck: dump source, check requirements.txt/go.mod for known CVEs.\n"
    ),
    "BBP": (
        "# Bug Bounty Soul\n\n"
        "Impact over noise. You are a professional bug bounty hunter inside a "
        "declared scope: enumerate carefully, hypothesize, verify, and only report "
        "what you can PROVE with a reproducing request pair (attack vs control). "
        "Chain findings to real impact (account takeover, data exposure, auth "
        "boundary breaks) — a reflected echo alone is nothing. RESPECT THE SCOPE "
        "in rules.md absolutely: no out-of-scope targets, no destructive testing, "
        "no automated brute force against real users. Evidence discipline: every "
        "confirmed finding is cataloged with a reproduce command, the exact "
        "request/response pair, and severity honest to its impact. Duplicates are "
        "waste — check what is already cataloged before re-proving.\n"
    ),
}
DEFAULT_RULES = {
    "CTF": (
        "# CTF Rules\n\n"
        "- The box is yours: full permission, no rules of engagement.\n"
        "- The flag string ends the engagement.\n"
        "- Services may be glitchy; retry before concluding.\n"
    ),
    "BBP": (
        "# Bug Bounty Rules\n\n"
        "- SCOPE: only targets the operator declared. Out-of-scope = never touched.\n"
        "- No destructive actions (deletion, mass-mail, defacement), no social engineering.\n"
        "- Rate-limit automated probing; you are a guest on production systems.\n"
        "- Report only verified, reproducible findings with evidence.\n"
    ),
}


def ensure_default_profiles() -> None:
    """Seed /profiles/CTF and /profiles/BBP if absent. Never raises."""
    try:
        import json

        from suijin.modules.platform.lib.workspace import profiles_root

        for name, soul in DEFAULT_SOULS.items():
            d = profiles_root() / name
            if (d / "SOUL.md").is_file():
                continue
            d.mkdir(parents=True, exist_ok=True)
            (d / "SOUL.md").write_text(soul, encoding="utf-8")
            (d / "rules.md").write_text(DEFAULT_RULES[name], encoding="utf-8")
            (d / "config.json").write_text(json.dumps({"adversary_profile": name}, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001 — seeding must never break boot
        pass


def list_profiles() -> list[str]:
    """Folder-backed profiles first (user-creatable), built-ins after, deduped."""
    names: list[str] = []
    try:
        from suijin.modules.platform.lib.workspace import profiles_root

        for d in sorted(profiles_root().iterdir()):
            if d.is_dir() and (d / "SOUL.md").is_file():
                names.append(d.name)
    except Exception:  # noqa: BLE001
        pass
    for name in PROFILES:
        if name not in names:
            names.append(name)
    return names


def _load_folder_profile(name: str) -> dict | None:
    """A file-backed profile: SOUL.md + rules.md become the directive,
    config.json becomes config overrides the caller merges."""
    try:
        import json

        from suijin.modules.platform.lib.workspace import profiles_root

        d = profiles_root() / name
        soul = d / "SOUL.md"
        if not soul.is_file():
            return None
        parts = []
        body = soul.read_text(encoding="utf-8", errors="ignore").strip()
        if body:
            parts.append(f"OPERATING PROFILE (from {name}/SOUL.md):\n{body}")
        rules_p = d / "rules.md"
        if rules_p.is_file():
            rules = rules_p.read_text(encoding="utf-8", errors="ignore").strip()
            if rules:
                parts.append(f"ENGAGEMENT RULES ({name}/rules.md) — binding:\n{rules}")
        cfg = {}
        cfg_p = d / "config.json"
        if cfg_p.is_file():
            try:
                loaded = json.loads(cfg_p.read_text(encoding="utf-8", errors="ignore"))
                if isinstance(loaded, dict):
                    cfg = loaded
            except ValueError:
                pass
        return {
            "directive": "\n\n".join(parts),
            "config_overrides": cfg,
            "source": str(d),
        }
    except Exception:  # noqa: BLE001
        return None


def get_profile(config: dict | None):
    """The active profile: a folder-backed one (SOUL/rules/config) when it
    exists, else a built-in dict. Folder profiles carry config_overrides the
    caller should merge over the run config."""
    name = str((config or {}).get("adversary_profile", "")).strip()
    if not name:
        return None
    for candidate in _profile_folder_candidates(name):
        folder = _load_folder_profile(candidate)
        if folder is not None:
            return folder
    return PROFILES.get(name.lower())


def _profile_folder_candidates(name: str) -> list[str]:
    """The name as-given, lowercased, and case-insensitively matched against
    the profiles dir (CTF == ctf == Ctf for folder lookup)."""
    cands = [name, name.lower()]
    try:
        from suijin.modules.platform.lib.workspace import profiles_root

        for d in profiles_root().iterdir():
            if d.is_dir() and d.name.lower() == name.lower() and d.name not in cands:
                cands.append(d.name)
    except Exception:  # noqa: BLE001
        pass
    return cands


def profile_directive(config: dict | None) -> str:
    """Prompt addition; empty string when no profile selected."""
    p = get_profile(config)
    if not p:
        return ""
    directive = p.get("directive") if isinstance(p, dict) else None
    return f"\n## {directive}\n" if directive else ""
