"""Hard Guardrail — deterministic, non-disableable check for
government/public/military/education domains.

Blocks .gov, .mil, .edu, .int and major intergovernmental orgs
regardless of project settings. This check cannot be toggled off.

Ported from redamon/agentic/orchestrator_helpers/hard_guardrail.py.
"""
from __future__ import annotations

import re

_TLD_PATTERNS = [
    r'\.gov$', r'\.gov\.[a-z]{2,3}$',
    r'\.gob\.[a-z]{2,3}$', r'\.gouv\.[a-z]{2,3}$',
    r'\.govt\.[a-z]{2,3}$', r'\.go\.[a-z]{2}$',
    r'\.gv\.[a-z]{2}$',
    r'\.mil$', r'\.mil\.[a-z]{2,3}$',
    r'\.edu$', r'\.edu\.[a-z]{2,3}$',
    r'\.ac\.[a-z]{2,3}$',
    r'\.int$',
]

_COMPILED_TLD_RE = re.compile(
    '|'.join(f'(?:{p})' for p in _TLD_PATTERNS), re.IGNORECASE
)

_EXACT_BLOCKED_DOMAINS: frozenset[str] = frozenset({
    'un.org', 'undp.org', 'unep.org', 'unicef.org', 'unhcr.org',
    'who.int', 'nato.int', 'icann.org', 'iana.org',
    'google.com', 'microsoft.com', 'apple.com', 'amazon.com',
    'facebook.com', 'meta.com', 'twitter.com', 'x.com',
    'cloudflare.com', 'akamai.com', 'fastly.com',
    'github.com', 'gitlab.com', 'bitbucket.org',
})


def is_hard_blocked(target: str) -> tuple[bool, str]:
    """Check if a target domain/IP is on the hard blocklist.

    Returns (blocked: bool, reason: str).
    """
    if not target:
        return False, ""

    target_lower = target.lower().strip()

    # Check TLD patterns
    if _COMPILED_TLD_RE.search(target_lower):
        return True, f"Blocked TLD pattern matched for: {target}"

    # Check exact domain matches
    for blocked in _EXACT_BLOCKED_DOMAINS:
        if target_lower == blocked or target_lower.endswith('.' + blocked):
            return True, f"Blocked domain: matches {blocked}"

    return False, ""
