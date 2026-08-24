"""Hill CTF seed data — users, docs, decoys, canary credentials.

The social engineering surface: convincing bait on the easy path, real
accounts on the hard one. Canary values are registered here so
defenses.py / events.py can wire trips.
"""

from __future__ import annotations

import hashlib

# ── real users (the JWT/IDOR surface) ──────────────────────────────────


def _pw(p: str) -> str:
    return hashlib.sha256(p.encode()).hexdigest()


USERS = {
    # username -> {id, pw_hash, role, email}
    "cartographer": {"id": 1, "pw": _pw("3l3vation!"), "role": "surveyor", "email": "cart@hill.local"},
    "wirewatcher": {"id": 2, "pw": _pw("s3ntinel#2026"), "role": "comms", "email": "wire@hill.local"},
    "gatekeeper": {"id": 3, "pw": _pw("Th3-Gate-101"), "role": "guard", "email": "gate@hill.local"},
}
# login hint left in a leaked doc (the intended discovery path):
#   "cartographer's password is a leetspeak of 'elevation' with a bang"
#   -> 3l3vation!

DOCS = {
    # doc_id -> {owner_id, title, body, classification}
    101: {
        "owner": 1,
        "title": "Trail Survey Notes",
        "classification": "internal",
        "body": "North slope survey complete. Reminder: the perimeter admin panel at /admin is "
        "DECOMMISSIONED — anything served there is legacy bait. Real entry is /hill/login. "
        "Password convention for field accounts: leetspeak + symbol (see onboarding doc).",
    },
    102: {
        "owner": 1,
        "title": "Key Fragment A (DO NOT SHARE)",
        "classification": "secret",
        "body": "VAULT KEY FRAGMENT A: HILLKEY-PARTA-7f3a9c\n\nStore nowhere else. The vault "
        "decryptor needs both fragments assembled as FRAGA-FRAGB.",
    },
    103: {
        "owner": 2,
        "title": "Comms Schedule",
        "classification": "internal",
        "body": "Weekly rotation call at dawn. If the vault token is compromised, the duty officer "
        "force-rotates via the local admin endpoint (/hill/admin/rotate) — red key required.",
    },
    104: {
        "owner": 3,
        "title": "Onboarding — credentials",
        "classification": "restricted",
        "body": "Field account passwords follow: leetspeak word + symbol. Cartographer's is derived "
        "from 'elevation'. Internal metadata service lives on the second port; never expose "
        "it publicly. Temp credentials from metadata expire in 20 minutes.",
    },
    105: {
        "owner": 2,
        "title": "Webhook Runbook",
        "classification": "internal",
        "body": "The /hill/api/webhook fetch feature reaches internal hosts only. Metadata endpoint: "
        "http://127.0.0.1:5911/metadata — respond with ?role=<role> for scoped temp creds. "
        "The AWS-style keys in the old docs are CANARIES — using them pages the SOC.",
    },
}

# ── decoys (canary-wired — touching these is loud) ─────────────────────

DECOY_TOKEN = "FLAG{hill_decoy_easy_path_taken}"  # validates FALSE always

DECOY_ENDPOINTS = {
    "/admin": {
        "kind": "fake_admin_panel",
        "body": {"app": "HillOps Console", "version": "0.9.1", "message": "authentication required"},
    },
    "/.git/config": {
        "kind": "git_bait",
        "body": '[core]\n\trepositoryformatversion = 0\n[remote "origin"]\n\turl = https://git.hill.local/hill/ops.git\n',
    },
}

# ── canary credentials (stage-2 bait; using them ANYWHERE = critical trip)

CANARY_AWS_KEY = "AKIAHILLCANARY01"
CANARY_AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/h1LLc4n4ryXaMPLEk3Y9"
# seeded in doc 106 (owner 3) — the tempting shortcut at the metadata stage
DOCS[106] = {
    "owner": 3,
    "title": "Legacy Service Account (DEPRECATED)",
    "classification": "restricted",
    "body": f"AWS_ACCESS_KEY_ID={CANARY_AWS_KEY}\nAWS_SECRET_ACCESS_KEY={CANARY_AWS_SECRET}\n"
    "(migrating off this account — still works against the metadata service afaik)",
}

CANARY_VALUES = {CANARY_AWS_KEY, CANARY_AWS_SECRET}

# ── misleading robots / security.txt (perimeter noise) ─────────────────

ROBOTS_TXT = """User-agent: *
Disallow: /admin
Disallow: /manager
Disallow: /backup
Disallow: /hill/login
# nothing else here. definitely no /vault.
"""

SECURITY_TXT = """Contact: mailto:security@hill.local
Expires: 2027-01-01T00:00:00Z
Preferred-Languages: en
Canonical: https://hill.local/.well-known/security.txt
Note: all endpoints require authorization. /manager is the admin console.
"""
# (/manager does not exist; /admin is a decoy; /hill/login is unlisted)
