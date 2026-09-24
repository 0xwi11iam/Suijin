"""Evidence vault (B13), finding dedup (B14), attack-path scoring (B15).

B13: findings carry captured evidence, hash-chained per engagement —
tampering with any stored evidence breaks the chain (verifiable).
B14: structural dedup — same root cause across paths collapses to one
finding with an occurrences list.
B15: probability-weighted attack-path scoring from finding chains.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _vault_dir() -> Path:
    from suijin.modules.platform.lib.workspace import artifact_dir

    d = artifact_dir("evidence")
    d.mkdir(parents=True, exist_ok=True)
    return d


def capture(finding: dict, evidence: str) -> dict:
    """B13: store evidence for a finding; returns the sealed record.
    The chain hash covers (prev_hash, finding summary, evidence) — any
    later edit breaks verification."""
    f = dict(finding)
    f["evidence_text"] = evidence[:10_000]
    f["captured_ts"] = __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime())
    chain = _load_chain()
    prev = chain[-1]["chain_hash"] if chain else "GENESIS"
    payload = json.dumps(
        {"prev": prev, "type": f.get("type"), "target": f.get("target"), "evidence": f["evidence_text"]}, sort_keys=True
    )
    f["prev_hash"] = prev
    f["chain_hash"] = hashlib.sha256(payload.encode()).hexdigest()
    chain.append(f)
    _save_chain(chain)
    return f


def verify_chain() -> tuple[bool, list[str]]:
    """B13: recompute the chain; returns (ok, problems)."""
    chain = _load_chain()
    problems = []
    prev = "GENESIS"
    for i, rec in enumerate(chain):
        payload = json.dumps(
            {
                "prev": prev,
                "type": rec.get("type"),
                "target": rec.get("target"),
                "evidence": rec.get("evidence_text", ""),
            },
            sort_keys=True,
        )
        expect = hashlib.sha256(payload.encode()).hexdigest()
        if rec.get("prev_hash") != prev:
            problems.append(f"record {i}: prev_hash broken (tampered or reordered)")
        if rec.get("chain_hash") != expect:
            problems.append(f"record {i}: chain_hash mismatch (evidence tampered)")
        prev = rec.get("chain_hash", "")
    return (not problems), problems


def dedup(findings: list) -> list:
    """B14: collapse same-root-cause findings. Key = (type, target,
    normalized evidence signature); duplicates merge into occurrences."""
    seen: dict[tuple, dict] = {}
    order = []
    for f in findings or []:
        sig = _signature(f)
        if sig in seen:
            seen[sig].setdefault("occurrences", []).append(f.get("path") or f.get("endpoint") or "?")
            continue
        merged = dict(f)
        merged["occurrences"] = [f.get("path") or f.get("endpoint") or "?"]
        seen[sig] = merged
        order.append(sig)
    return [seen[s] for s in order]


def _signature(f: dict) -> tuple:
    ev = " ".join(str(f.get("evidence", "")).lower().split())[:80]
    return (str(f.get("type", "")).lower(), str(f.get("target", "")).lower(), ev)


def score_paths(findings: list) -> str:
    """B15: chain findings into attack paths; weight by confidence."""
    w = {"verified": 0.9, "probable": 0.6, "suspected": 0.3}
    steps = sorted(findings or [], key=lambda f: str(f.get("phase", "")))
    if not steps:
        return "no findings to score"
    paths = []
    cur, cur_p = [], 1.0
    for f in steps:
        p = w.get(str(f.get("confidence", "probable")), 0.6)
        cur.append(f"{f.get('type', '?')}@{str(f.get('target', '?'))[:20]}")
        cur_p *= p
        if f.get("type") in ("rce", "privesc", "auth_bypass", "sqli") or len(cur) >= 4:
            paths.append((cur, cur_p))
            cur, cur_p = [], 1.0
    if cur:
        paths.append((cur, cur_p))
    # headline: the FULL chain end-to-end (the kill-path view)
    full_p = 1.0
    for f in steps:
        full_p *= w.get(str(f.get("confidence", "probable")), 0.6)
    paths.append(([f"{f.get('type', '?')}" for f in steps], full_p))
    if not paths:
        return "no complete attack path"
    lines = ["attack paths (probability-weighted):"]
    for i, (path, p) in enumerate(sorted(paths, key=lambda x: -x[1])[:5], 1):
        lines.append(f"  {i}. {p:.2f}  {' -> '.join(path)}")
    return "\n".join(lines)


def _chain_path() -> Path:
    return _vault_dir() / "chain.json"


def _load_chain() -> list:
    p = _chain_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_chain(chain: list) -> None:
    _chain_path().write_text(json.dumps(chain, indent=2))
