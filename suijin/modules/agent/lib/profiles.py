"""Adversary profiles (A10) — persona-driven engagement style.

config.json: "adversary_profile": "stealth_apt" | "script_kiddie" | "insider"
Each profile injects a directive into the system prompt (tool selection,
pacing, noise) so the same objective is pursued with a different posture.
"""

PROFILES: dict[str, dict] = {
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


def get_profile(config: dict | None) -> dict | None:
    name = str((config or {}).get("adversary_profile", "")).lower().strip()
    return PROFILES.get(name)


def profile_directive(config: dict | None) -> str:
    """Prompt addition; empty string when no profile selected."""
    p = get_profile(config)
    return f"\n## {p['directive']}\n" if p else ""
