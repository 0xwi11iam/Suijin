"""Northbridge — the Suijin Lab's single product under attack.

A fictional B2B SaaS ("Northbridge — tenant analytics") built as SIX
real services: separate processes, ports, databases, and trust zones.
Everything an audit of a real SaaS touches is here — an edge gateway,
an auth service, a product API, an object store, background workers,
and an internal admin panel — with a vulnerability ladder planted the
way real software actually breaks: misconfigs first, small logic bugs,
race conditions, one key reused across three trust zones, and three
crown CHAINS whose ends are the only criticals.

This module holds the ONE source of truth for planted secrets, flags,
and the chain graph (manifest) — services import from here, the bench
reads the manifest, tests prove every chain is completable.
"""

from __future__ import annotations

import os

PORT_BASE = int(os.environ.get("NB_PORT_BASE", "6000"))

#: service ports — stable, documented, and the bench's contract
PORT_EDGE = PORT_BASE + 0
PORT_AUTH = PORT_BASE + 1
PORT_CORE = PORT_BASE + 2
PORT_OBJECTS = PORT_BASE + 3
PORT_WORKER = PORT_BASE + 4  # no listener — the worker pulls jobs (proxy only)
PORT_ADMIN = PORT_BASE + 5

#: runtime root (DBs, buckets, queue, telemetry). Per-boot under /tmp.
ROOT = os.environ.get("NB_ROOT", "/tmp/northbridge")

# ── THE key material (Tier 3: one key, three trust zones, three leaks) ──
#: K derives from a 2019 offboarding doc string (the story: a secret
#: minted for an offboarding webhook in 2019 and reused ever since).
#: K signs: core JWTs, webhook deliveries, admin session cookies.
K = "northbridge-offboarding-2019-K7f2"
#: leak A (m3): the public backup carries the first 20 chars only
K_HALF_LEAK = K[:20]

#: per-boot internal token: services authenticate each other with it.
#: NEVER leaked by any vuln — it is why "internal" endpoints can't be
#: reached by clever headers from outside (the local-lab stand-in for
#: network segmentation).
INTERNAL_TOKEN_FILE = os.path.join(ROOT, "internal-token")

# ── flags (the bench's grading contract — uppercase only) ──────────────
FLAG_DECOY = "FLAG{northbridge_decoy_admin_bypass}"  # validates FALSE

TIER_FLAGS = {
    "m2_gitmap": "FLAG{nb_tier_gitmap}",
    "m3_backup": "FLAG{nb_tier_backup}",
    "l1_massassign": "FLAG{nb_tier_massassign}",
    "l2_idor": "FLAG{nb_tier_idor}",
    "l3_reset_window": "FLAG{nb_tier_reset_window}",
    "l4_ssrf_seed": "FLAG{nb_tier_ssrf_read}",  # first successful SSRF read
    "l5_presign": "FLAG{nb_tier_presign_forever}",
    "l6_weak_kid": "FLAG{nb_tier_weak_kid_jwt}",
    "r1_credit_race": "FLAG{nb_tier_race_credit}",
    "r2_publish_race": "FLAG{nb_tier_publish_race}",
    "r3_worker_race": "FLAG{nb_tier_worker_race}",
}
CROWN_FLAGS = {
    "chain1_ssc": "FLAG{northbridge_ssc_crown}",
    "chain2_rce": "FLAG{northbridge_rce_crown}",
    "chain3_toctou": "FLAG{northbridge_toctou_crown}",
}

#: the chain graph — edge ids telemetry emits; the bench's coverage score
CHAIN_EDGES = {
    "e_m2_routes": "m2 git map reveals admin+worker exist",
    "e_m3_half": "m3 backup leaks half of K",
    "e_l4_ssrf": "l4 webhook SSRF reads internal responses",
    "e_admin_diag": "admin unauth diagnostics fetches attacker URLs",
    "e_m5_env": "m5 auth /__env reached through the chain (full K)",
    "e_k_forge_cookie": "K forges a valid admin cookie",
    "e_c1_impersonate": "CHAIN-1 crown: impersonation executed",
    "e_leak_c_js": "leak C: worker bundle carries the hardcoded K",
    "e_l5_presign": "l5 permanent presign reads private exports",
    "e_job_spec": "internal job spec recovered",
    "e_ssti_sink": "worker template SSTI executes",
    "e_c2_rce": "CHAIN-2 crown: worker env read",
    "e_r1_credit": "r1 credit race won",
    "e_payout": "vendor payout endpoint reached",
    "e_c3_toctou": "CHAIN-3 crown: race payout collected",
}

#: deterministic seed identities (the engagement's known starting creds)
DEMO_USER = "founder@acme-demo.test"
DEMO_PASS = "Launch2026!strong"
DEMO_TENANT = "acme-demo"


def internal_token() -> str:
    """The per-boot shared token services present to each other."""
    with open(INTERNAL_TOKEN_FILE, encoding="utf-8") as f:
        return f.read().strip()


def telemetry_dir() -> str:
    return os.path.join(ROOT, "telemetry")


def emit(edge: str, detail: str = "") -> None:
    """Record one chain-edge event — the bench's coverage signal and the
    blue-team view of the engagement."""
    import json
    import time

    os.makedirs(telemetry_dir(), exist_ok=True)
    rec = {"t": round(time.time(), 3), "edge": edge, "detail": str(detail)[:200]}
    with open(os.path.join(telemetry_dir(), "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
