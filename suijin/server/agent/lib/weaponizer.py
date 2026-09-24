"""Weaponizer — escalation proposals on CONFIRMED findings.

When catalog_exploit lands CONFIRMED, this builds the ready-to-fire
deepening task for that vulnerability class and proposes it to the model
as a deploy_subagent one-decision action (propose-only by operator
design: the model pulls the trigger, one turn cost, no auto-spawn).

Playbooks are seeded from the supervisor's exploitation follow-through
vocabulary, upgraded from tool-name hints to step sequences.
"""

from __future__ import annotations

import re

# catalog_exploit result text carries: verdict, class, entry id, title
_CONF_RE = re.compile(r"CONFIRMED", re.I)
_EXP_RE = re.compile(r"(EXP-\d+)")
_CLASS_HINTS = (
    "sql",
    "command",
    "cmdi",
    "rce",
    "ssti",
    "ssrf",
    "upload",
    "auth",
    "bypass",
    "jwt",
    "xxe",
    "lfi",
    "traversal",
    "deser",
    "idor",
    "xss",
    "redirect",
    "race",
    "xxe",
)

PLAYBOOKS: dict[str, dict] = {
    "sql": {
        "skill": "sql_injection",
        "steps": (
            "enumerate column count via ORDER BY, confirm UNION alignment, "
            "dump sqlite_master/information_schema table names, extract users/creds tables, "
            "read any secret columns; sqlmap --batch on the confirmed param as the parallel path"
        ),
    },
    "command": {
        "skill": "rce",
        "steps": (
            "stabilize the injection ( separators: ; | $( ) ` `), verify id/whoami, "
            "upgrade to a shell via rev_shell/stager, enumerate the box: sudo -l, SUID, "
            "writable services, credentials in env/config files"
        ),
    },
    "rce": {
        "skill": "rce",
        "steps": "verify execution determinism, write a webshell for persistence of access, run the privesc checklist, loot flag paths",
    },
    "ssti": {
        "skill": "rce",
        "steps": "confirm the engine ({{7*7}} style probes), escape the sandbox, file read via the template engine, command execution, loot config secrets",
    },
    "ssrf": {
        "skill": "ssrf",
        "steps": (
            "sweep internal ports via the SSRF primitive, fetch cloud metadata "
            "(169.254.169.254 / 127.0.0.1 metadata routes), validate any returned creds, "
            "chain into internal admin surfaces"
        ),
    },
    "upload": {
        "skill": "file_upload",
        "steps": "map allowed extensions, upload a code-bearing variant (.phtml/.pyc/renamed), locate the served path, execute, verify shell output",
    },
    "auth": {
        "skill": "access_control",
        "steps": "map every authenticated surface reachable with the bypassed session, diff admin vs user visibility, harvest role-gated data and function references",
    },
    "jwt": {
        "skill": "jwt",
        "steps": "crack the signing secret if weak (jwt_crack), forge the admin role claim, replay against every privileged endpoint, test alg:none acceptance on the API surface",
    },
    "xxe": {
        "skill": "xxe",
        "steps": "read local files (/etc/passwd, app config, env), blind OOB exfil if errors are swallowed, SSRF via external entities to internal routes",
    },
    "lfi": {
        "skill": "path_traversal",
        "steps": "read config/env files for secrets, log poisoning for code execution if a writable path pairs with an include, wrapper attacks (php://filter) where the stack allows",
    },
    "deser": {
        "skill": "deser",
        "steps": "identify the serializer, craft the gadget chain for the stack, verify with a benign marker before destructive payloads, then code execution",
    },
    "idor": {
        "skill": "access_control",
        "steps": "enumerate the object id space around the confirmed id, harvest role-mismatched data at scale (bounded samples), map which object types share the flaw",
    },
    "xss": {
        "skill": "xss",
        "steps": (
            "IMPACT EXPLORATION is the deliverable (a reflection alone is a lead, not a finding): "
            "1) cookie/token theft — document.cookie exfil to a listener you control (ssrf_canary); "
            "2) OAuth chaining — you mapped state/redirect_uri already: craft the XSS to capture the "
            "code/state from the callback URL, replay the flow stolen; "
            "3) authenticated read — run a fetch() as the victim against a role-gated endpoint "
            "(the web_session worklist tells you which), exfil the body; "
            "4) CSRF token harvest to unlock a second csrf-protected action. "
            "Verify execution in the browser (mcp_browser_goto + snapshot) — raw HTML reflection is "
            "never a zero-interaction claim"
        ),
    },
    "redirect": {
        "skill": "open_redirect",
        "steps": "test OAuth/token flows through the redirect (token theft), CRLF injection for cache poisoning, then SSRF chaining via same-origin redirects",
    },
    "race": {
        "skill": "race",
        "steps": "parallel-fire the window (16+ concurrent), prove multi-spend/oversubscription in the response deltas, document the business impact precisely",
    },
}
_DEFAULT_PLAYBOOK = {
    "skill": "post_exploit",
    "steps": "deepen the confirmed primitive: enumerate what else the same flaw class exposes on adjacent endpoints, then loot and document",
}


def _class_of(blob: str) -> str:
    b = (blob or "").lower()
    for key in PLAYBOOKS:
        if key in b:
            return key
    return ""


def propose_for(result: dict, already: set) -> tuple[str, str] | None:
    """(proposal_message, exp_id) when a CONFIRMED catalog_exploit step is
    found and not yet proposed; None otherwise. Never raises."""
    try:
        texts = []
        step = result.get("_current_step") or {}
        texts.append(str(step.get("tool_name") or "") + " " + str(step.get("tool_output") or ""))
        for tr in (result.get("execution_trace") or [])[-2:]:
            texts.append(str(tr.get("tool_name") or "") + " " + str(tr.get("tool_output") or "")[:3000])
        blob = "\n".join(texts)
        if "catalog" not in blob.lower() or not _CONF_RE.search(blob):
            return None
        m = _EXP_RE.search(blob)
        exp_id = m.group(1) if m else ""
        key = exp_id or blob[:80]
        if key in already:
            return None
        cls = _class_of(blob)
        pb = PLAYBOOKS.get(cls, _DEFAULT_PLAYBOOK)
        title_m = re.search(r"(?:title|finding)\s*[:=]\s*['\"]?([^'\"\n]{6,90})", blob, re.I)
        title = title_m.group(1).strip() if title_m else "confirmed finding"
        task = f"Escalate confirmed {cls or 'finding'} — {title}. Steps: {pb['steps']}. (finding {exp_id or key[:12]})"
        msg = (
            f"ESCALATION READY (finding {exp_id or 'confirmed'} — {title[:60]}): the confirmed "
            f"{cls or 'finding'} primitive has a standard deepening path and the engagement is not "
            f"mid-critical-turn. One decision fires a parallel specialist team:\n"
            f'  deploy_subagent "{task}"\n'
            f"Doctrine reference for the team: {pb['skill']}. Fire it, or state why not in your thought."
        )
        return msg, key
    except Exception:  # noqa: BLE001 — proposals must never break the loop
        return None
