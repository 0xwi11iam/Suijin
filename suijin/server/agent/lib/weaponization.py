"""Weaponization — the go-bigger engine (2026-10-20).

Field pattern that motivated this: the agent catalogs misconfigs, bundle
secrets, and header trivia run after run because the coverage gate
counted every cell equally — an `info@target` cell bought completion as
cheaply as an `authn@target` cell, and one is a curl while the other is
twenty iterations of creative work. The agent played the game the gates
defined, and the game rewarded volume over impact.

This module is the single source of truth for IMPACT. Everything that
gates, orders, or nags reads these weights:

- CLASS_WEIGHTS     every coverage class weighted by attack value
- satiation()       trivia counter — fires the DEPTH DIRECTIVE
- attack_scenarios() deterministic chain hypotheses from confirmed pairs
- weapons_block()   the depth tools the agent forgets it has
- directive()       the rendered DEPTH DIRECTIVE text

Operator rulings (2026-10-20):
- low-weight cells (info, redirect) NEVER block completion — optional
- satiation fires at 4 trivia findings with 0 real ones
- phase promotion stays pressure-only (no forced transitions)
"""

from __future__ import annotations

#: The 15 coverage classes, weighted. Aligned with the exploit catalog's
#: severity ladder (class defaults): cmdi/ssti are critical; authn/authz/
#: idor/mass_assignment/sqli are the crown-jewel classes; ssrf/lfi/race/
#: xxe/upload are high-value but situational; xss is medium; redirect
#: and info are recon output, not findings.
CLASS_WEIGHTS: dict[str, int] = {
    "cmdi": 15,
    "ssti": 15,
    "authn": 12,
    "authz": 12,
    "idor": 11,
    "mass_assignment": 10,
    "sqli": 10,
    "upload": 8,
    "xxe": 8,
    "ssrf": 8,
    "lfi": 8,
    "race": 8,
    "xss": 5,
    "redirect": 2,
    "info": 1,
}

#: Cells below this weight never block completion (operator ruling:
#: optional cells). xss (5) still blocks; redirect (2) and info (1)
#: do not.
BLOCKING_MIN_WEIGHT = 5

#: Satiation thresholds (operator ruling): 4+ trivia findings with zero
#: real ones means the passive surface is tapped out.
SATIATION_TRIVIA = 4
#: Findings at/above this weight count as "real" for satiation.
REAL_FINDING_WEIGHT = 8

#: Severity-word → weight for findings harvested from catalog output
#: (titles carry the classed prefix, e.g. "CRITICAL CVSS 9.8 : ...").
_SEVERITY_WEIGHTS = {"critical": 15, "high": 10, "medium": 5}


def class_weight(vuln_class: str) -> int:
    """Weight for a coverage class. Unknown classes weigh 5 (medium):
    honest for a class we haven't profiled rather than assuming trivia."""
    return CLASS_WEIGHTS.get(str(vuln_class or "").strip().lower(), 5)


def finding_weight(finding: dict) -> int:
    """Weight for a confirmed finding. Reads the severity word the
    catalog prefixes into titles ("HIGH CVSS 7.2 : reflected XSS..."),
    falling back to explicit severity fields. Default 1 — unparseable
    findings are trivia until proven otherwise."""
    if not isinstance(finding, dict):
        return 1
    t = str(finding.get("title") or "").upper()
    for word, w in (("CRITICAL", 15), ("HIGH", 10), ("MEDIUM", 5), ("LOW", 2), ("INFO", 1)):
        if t.startswith(word):
            return w
    sev = str(finding.get("system_severity") or finding.get("severity") or "").strip().lower()
    if sev in _SEVERITY_WEIGHTS:
        return _SEVERITY_WEIGHTS[sev]
    klass = str(finding.get("class") or finding.get("vuln_class") or "").strip().lower()
    if klass:
        return class_weight(klass)
    return 1


def satiation(state: dict) -> dict:
    """Trivia satiation over confirmed findings. Saturated = the passive
    surface is tapped out: plenty of trivia, nothing with teeth."""
    founds = [f for f in (state.get("findings") or []) if isinstance(f, dict)]
    trivia = [f for f in founds if finding_weight(f) <= 2]
    real = [f for f in founds if finding_weight(f) >= REAL_FINDING_WEIGHT]
    return {
        "saturated": len(trivia) >= SATIATION_TRIVIA and not real,
        "trivia": len(trivia),
        "real": len(real),
    }


def directive(state: dict) -> str:
    """The DEPTH DIRECTIVE — injected into DRIVE every turn while
    saturated, until a real test or real finding lands. Empty when not
    saturated."""
    s = satiation(state)
    if not s["saturated"]:
        return ""
    lines = [
        f"**DEPTH DIRECTIVE — SURFACE SATURATED**: {s['trivia']} informational findings "
        f"cataloged, zero high-impact ones. The next bundle secret or missing header "
        "adds NOTHING. Passive recon has told you everything it will.",
        "SHIFT NOW to active testing: auth flows, IDOR matrices, business logic, "
        "parameter tampering. Weapons: auth_forge, cred_matrix, race_test, "
        "mass_assign, graphql_attack, param_pollute.",
    ]
    if str(state.get("current_phase") or "").lower() in ("recon", "informational", ""):
        lines.append("PROMOTE to exploitation THIS TURN — mapping is done.")
    return "\n".join(lines) + "\n"


#: Scenario templates: (keywords_a, keywords_b, hypothesis). A pair of
#: confirmed findings matching both sides suggests the chain. Matched
#: against title+class text, case-insensitive; order-independent.
#: Scenario templates: (keywords_a, keywords_b, hypothesis, classes).
#: `classes` = the coverage classes whose tested_* marks CLEAR the
#: candidate obligation (machine-checkable exit, B 2026-10-20).
_SCENARIO_PATTERNS: list[tuple[tuple[str, ...], tuple[str, ...], str, tuple[str, ...]]] = [
    (
        ("redirect", "open redirect"),
        ("oauth", "callback", "token", "auth", "login", "session"),
        "Open redirect × auth flow: can the redirector intercept an OAuth callback "
        "or login return URL? Token/code theft via the redirector is a HIGH — test it.",
        ("redirect", "authn"),
    ),
    (
        ("idor", "object", "bola", "horizontal"),
        ("admin", "internal", "private", "api", "endpoint"),
        "Object access × private surface: replay the IDOR against every internal/admin "
        "endpoint you discovered. Cross-tenant object reach is a CRITICAL.",
        ("idor", "authz"),
    ),
    (
        ("ssrf", "server-side request"),
        ("metadata", "internal", "admin", "credential", "cloud"),
        "SSRF × internal surface: point the fetcher at cloud metadata / internal admin "
        "ports. Credential theft via SSRF is a HIGH-to-CRITICAL.",
        ("ssrf",),
    ),
    (
        ("jwt", "token", "session", "auth bypass", "forgery"),
        ("api", "endpoint", "graphql", "rest"),
        "Identity control × API surface: replay the bypassed/forged identity against "
        "every authenticated endpoint, especially admin ones. Vertical reach is a CRITICAL.",
        ("authn", "authz", "idor"),
    ),
    (
        ("key", "secret", "credential", "api key", "leak", "token"),
        ("api", "endpoint", "upload", "admin", "internal"),
        "Leaked secret × authenticated surface: replay the leaked credential against "
        "every private endpoint it names. Live privileged use is a HIGH.",
        ("authn", "authz"),
    ),
    (
        ("upload", "file"),
        ("exec", "include", "path", "template", "render"),
        "Upload × execution surface: can an uploaded file reach an include path, template "
        "engine, or served-exec context? Upload-to-RCE is a CRITICAL.",
        ("upload", "cmdi", "ssti", "lfi"),
    ),
    (
        ("xss", "script"),
        ("admin", "panel", "dashboard", "moderator", "internal"),
        "XSS × privileged surface: is there an admin/moderator panel where stored XSS "
        "fires? Privileged-session XSS is a HIGH.",
        ("xss",),
    ),
    (
        ("race", "concurrent", "duplicate"),
        ("pay", "price", "balance", "credit", "withdraw", "transfer", "coupon", "vote"),
        "Race × stateful action: fire the concurrent burst on the money/vote action. Double-spend via race is a HIGH.",
        ("race",),
    ),
]

_FALLBACK_SCENARIO = (
    "Combine what you hold: A gives you {a}, B gives you {b} — what does an attacker "
    "do NEXT holding both? Pick the pair and test the chain."
)


def attack_scenarios(state: dict, limit: int = 3) -> list[str]:
    """Deterministic chain hypotheses from confirmed finding pairs.
    Only pairs where at least one side has weight ≥ 5 — chaining two
    trivia findings is noise. Ordered by combined weight, capped."""
    founds = [f for f in (state.get("findings") or []) if isinstance(f, dict)]
    if len(founds) < 2:
        return []
    scored: list[tuple[int, str]] = []
    for i in range(len(founds)):
        for j in range(i + 1, len(founds)):
            a, b = founds[i], founds[j]
            wa, wb = finding_weight(a), finding_weight(b)
            if max(wa, wb) < 5:
                continue
            ta = str(a.get("title") or a.get("description") or "")[:160].lower()
            tb = str(b.get("title") or b.get("description") or "")[:160].lower()
            for kws_a, kws_b, hypothesis, _classes in _SCENARIO_PATTERNS:
                a_match = any(k in ta for k in kws_a) and any(k in tb for k in kws_b)
                b_match = any(k in tb for k in kws_a) and any(k in ta for k in kws_b)
                if a_match or b_match:
                    scored.append((wa + wb, hypothesis))
                    break
    if not scored and founds:
        # no pattern matched but the agent holds real findings — generic
        # combinatorial push with the two heaviest
        heavy = sorted(founds, key=finding_weight, reverse=True)[:2]
        if finding_weight(heavy[0]) >= 5:
            scored.append(
                (
                    finding_weight(heavy[0]) + finding_weight(heavy[1]),
                    _FALLBACK_SCENARIO.format(
                        a=str(heavy[0].get("title") or "?")[:60],
                        b=str(heavy[1].get("title") or "?")[:60],
                    ),
                )
            )
    scored.sort(key=lambda x: x[0], reverse=True)
    seen: list[str] = []
    for _, h in scored:
        if h not in seen:
            seen.append(h)
    return seen[:limit]


#: The depth tools the agent under-uses, with the one-line "what it's
#: for" the system prompt catalog buries.
WEAPONS_BLOCK = """## WEAPONS — depth tools built for this phase
- **auth_forge** — mint 10 JWT/token confusion variants (alg-none, key-confusion, kid-injection, claim-escalation) and replay them against a URL
- **cred_matrix** — systematic IDOR sweep: one object ID axis × every role/credential you hold
- **race_test** — concurrent burst a stateful endpoint (double-spend, limit bypass, mass vote)
- **mass_assign** — inject role/isAdmin/userId fields into a write endpoint
- **graphql_attack** — introspection, depth, aliasing, and mutation probing
- **param_pollute** — HTTP parameter pollution variants across params
- **jwt_decode** — inspect any token's header/payload/signature first

One CRITICAL chained finding is worth more than fifty informational ones. These tools are how you get there.
"""


def weapons_block(state: dict | None = None) -> str:
    """Rendered in the exploitation/post-exploitation context (unused
    args keep the section-renderer signature uniform)."""
    return WEAPONS_BLOCK


# ── B (2026-10-20): CHAIN CANDIDATES AS OBLIGATIONS ─────────────────
# Detected finding pairs become completion-blocking work items (the
# reference insight: "no active chain opportunities remain unexplored").
# Derived deterministically from state["findings"] every turn — only
# the explored MARKS live in state (_chain_explored, persisted via F1).
# Machine-checkable exits: a tested_* coverage mark on any of the
# pattern's classes clears it; the shared 5-refusal stand-down is the
# deadlock backstop, same as every other gate.

_MAX_ACTIVE_CANDIDATES = 3


def _candidate_id(a: dict, b: dict) -> str:
    import hashlib

    titles = sorted(
        [
            str(a.get("id") or a.get("title") or "")[:80],
            str(b.get("id") or b.get("title") or "")[:80],
        ]
    )
    return hashlib.sha1("|".join(titles).encode()).hexdigest()[:12]


def chain_candidates(state: dict) -> list[dict]:
    """Derive chain candidates from confirmed finding pairs. Same
    pattern engine as attack_scenarios, but returns obligation records
    {id, hypothesis, findings, classes} capped at the top by combined
    weight. Trivia pairs excluded (max pair weight ≥ 5)."""
    founds = [f for f in ((state or {}).get("findings") or []) if isinstance(f, dict)]
    if len(founds) < 2:
        return []
    out: list[tuple[int, dict]] = []
    for i in range(len(founds)):
        for j in range(i + 1, len(founds)):
            a, b = founds[i], founds[j]
            wa, wb = finding_weight(a), finding_weight(b)
            if max(wa, wb) < 5:
                continue
            ta = str(a.get("title") or a.get("description") or "")[:160].lower()
            tb = str(b.get("title") or b.get("description") or "")[:160].lower()
            for kws_a, kws_b, hypothesis, classes in _SCENARIO_PATTERNS:
                a_match = any(k in ta for k in kws_a) and any(k in tb for k in kws_b)
                b_match = any(k in tb for k in kws_a) and any(k in ta for k in kws_b)
                if a_match or b_match:
                    out.append(
                        (
                            wa + wb,
                            {
                                "id": _candidate_id(a, b),
                                "hypothesis": hypothesis,
                                "findings": [
                                    str(a.get("title") or "?")[:60],
                                    str(b.get("title") or "?")[:60],
                                ],
                                "classes": list(classes),
                            },
                        )
                    )
                    break
    out.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    uniq: list[dict] = []
    for _, c in out:
        if c["id"] not in seen:
            seen.add(c["id"])
            uniq.append(c)
    return uniq[:_MAX_ACTIVE_CANDIDATES]


def unexplored_candidates(state: dict) -> list[dict]:
    """Candidates not yet cleared. Clears on: an explored mark in
    state['_chain_explored'], OR a tested_* coverage mark on any of the
    pattern's classes (the ledger is the shared evidence store)."""
    import contextlib

    cands = chain_candidates(state)
    if not cands:
        return []
    explored = {
        str(k) for k in ((state or {}).get("_chain_explored") or {}) if (state.get("_chain_explored") or {}).get(k)
    }
    tested_classes: set[str] = set()
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.coverage import _load

        for key, c in (_load().get("cells") or {}).items():
            if c.get("status") in ("tested_vulnerable", "tested_not_vulnerable", "not_applicable"):
                tested_classes.add(str(key).rsplit("|", 1)[-1])
    live = []
    for c in cands:
        if c["id"] in explored:
            continue
        if any(cl in tested_classes for cl in c["classes"]):
            continue
        live.append(c)
    return live


#: A (2026-10-20): ANTI-GAMING RED FLAGS — adapted from the reference
#: methodology engines (detectRedFlags): the ledger shape itself tells
#: you when the agent is marking cells instead of testing classes.
#: Each flag is a completion blocker until the ledger shape changes.
def red_flags(state: dict) -> list[str]:
    """Deterministic anti-gaming flags over the coverage ledger + trace.

    - bulk_copy_paste: ≥5 cells sharing the same evidence string
    - too_fast: ≥10 marked cells but <5 successful HTTP-ish tool calls
      in the whole trace (marks without requests = paperwork, not tests)
    - all_not_applicable: >60% of marked cells are N/A ("may be avoiding
      work" — the honest N/A rate on a real target is far lower)
    """
    import contextlib

    flags: list[str] = []
    with contextlib.suppress(Exception):
        from collections import Counter

        from suijin.modules.tools.lib.coverage import _load

        cells = [c for c in (_load().get("cells") or {}).values() if isinstance(c, dict)]
        marked = [
            c for c in cells if c.get("status") in ("tested_vulnerable", "tested_not_vulnerable", "not_applicable")
        ]
        if not marked:
            return []

        ev_counts = Counter(
            str(c.get("evidence") or "").strip() for c in marked if str(c.get("evidence") or "").strip()
        )
        for ev, n in ev_counts.most_common(1):
            if n >= 5:
                flags.append(
                    f"bulk_copy_paste: {n} coverage cells share one identical evidence string — "
                    "re-test each class individually or write real per-class evidence"
                )
                break

        na = sum(1 for c in marked if c.get("status") == "not_applicable")
        if na and na / len(marked) > 0.6 and len(marked) >= 8:
            flags.append(
                f"all_not_applicable: {na}/{len(marked)} marked cells are not_applicable "
                "(>60%) — the honest N/A rate is far lower; justify each or test them"
            )

        if len(marked) >= 10:
            trace = list((state or {}).get("execution_trace") or [])
            http_ish = sum(
                1
                for t in trace
                if isinstance(t, dict)
                and t.get("success", True)
                and str(t.get("tool_name") or "")
                in (
                    "http_request",
                    "http_replay",
                    "http_replay_raw",
                    "inject_probe",
                    "execute_terminal",
                    "auth_forge",
                    "cred_matrix",
                    "bypass_403",
                    "catalog_exploit",
                )
            )
            if http_ish < 5:
                flags.append(
                    f"too_fast: {len(marked)} cells marked with only {http_ish} actual probe-class calls in the "
                    "trace — you cannot have tested that many classes; mark what you actually tested"
                )
    return flags
