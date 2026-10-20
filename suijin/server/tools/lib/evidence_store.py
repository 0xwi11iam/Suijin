"""Tamper-evident PoC evidence store (2026-10-20, ARTEX evidence/ adaption).

Confirmed findings deserve proof that PROVABLY hasn't changed since
verification — for bug bounty submissions and for the operator's own
trust in the catalog. The design (adapted, simplified for our file-
based engagement layout):

- CAPTURE at CONFIRMED time: the verifying output + the POC contract
  (exploit.yaml content + expected marker) are written as
  content-addressed blobs: exploits/<id>/evidence/<h[:2]>/<sha256>.bin
- MANIFEST: evidence.json carries {role, hash, len, captured_at,
  algo} — the manifest's own integrity field is the sha256 of its
  normalized core (id+role+hash+len), so a edited manifest trips
  verification even if the blob is re-hashed consistently.
- VERIFY at report time: re-hash every blob, re-derive every manifest
  self-hash. Any drift returns a per-entry warning that the final
  report renders ("evidence changed after verification").

Best-effort like everything audit-side: never raises into the run.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from pathlib import Path


def _entry_root(engagement_dir, entry_id: str) -> Path:
    return Path(engagement_dir) / "exploits" / str(entry_id)


def _blobs_root(entry_root: Path) -> Path:
    return entry_root / "evidence"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def capture(entry_id: str, role: str, content: str, engagement_dir) -> dict | None:
    """Write one evidence blob + manifest row. role: proof | baseline |
    verification | supporting. Returns the manifest record (or None on
    failure — capture must never break a confirmation)."""
    try:
        eid = str(entry_id or "").strip()
        body = str(content or "").encode("utf-8", errors="replace")
        if not eid or not body:
            return None
        h = _sha(body)
        root = _blobs_root(_entry_root(engagement_dir, eid))
        blob = root / h[:2] / f"{h}.bin"
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(body)
        record = {
            "role": str(role or "proof")[:24],
            "hash": h,
            "algo": "sha256",
            "len": len(body),
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        record["self"] = _sha(json.dumps({k: record[k] for k in ("role", "hash", "len")}, sort_keys=True).encode())
        # merge into the entry manifest
        mpath = root / "evidence.json"
        manifest = {"records": []}
        if mpath.exists():
            with contextlib.suppress(Exception):
                manifest = json.loads(mpath.read_text(encoding="utf-8")) or {"records": []}
        recs = manifest.setdefault("records", [])
        if not any(r.get("hash") == h for r in recs):
            recs.append(record)
            with contextlib.suppress(Exception):
                mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return record
    except Exception:  # noqa: BLE001
        return None


def verify(engagement_dir, entry_id: str = "") -> dict:
    """Re-hash every blob + re-derive manifest self-hashes. Returns
    {entries: {eid: [problems...]}} — empty lists = clean. Used by the
    report path; drift renders as a staleness warning."""
    out: dict[str, list[str]] = {}
    try:
        base = Path(engagement_dir) / "exploits"
        if not base.exists():
            return {"entries": {}}
        roots = [_entry_root(engagement_dir, entry_id)] if entry_id else sorted(base.iterdir())
        for root in roots:
            eid = root.name
            problems: list[str] = []
            mpath = root / "evidence" / "evidence.json"
            if not mpath.exists():
                continue
            try:
                manifest = json.loads(mpath.read_text(encoding="utf-8"))
            except ValueError:
                out[eid] = ["evidence.json unreadable (corrupt manifest)"]
                continue
            for rec in manifest.get("records", []):
                h = str(rec.get("hash") or "")
                role = str(rec.get("role") or "?")
                blob = root / "evidence" / h[:2] / f"{h}.bin"
                if not blob.exists():
                    problems.append(f"{role}: blob missing ({h[:12]})")
                    continue
                data = blob.read_bytes()
                if _sha(data) != h:
                    problems.append(f"{role}: blob hash DRIFT ({h[:12]} recorded, {_sha(data)[:12]} now)")
                elif len(data) != int(rec.get("len") or -1):
                    problems.append(f"{role}: length drift ({rec.get('len')} recorded, {len(data)} now)")
                expect_self = _sha(
                    json.dumps({k: rec.get(k) for k in ("role", "hash", "len")}, sort_keys=True).encode()
                )
                if str(rec.get("self") or "") != expect_self:
                    problems.append(f"{role}: manifest self-hash mismatch (edited after capture?)")
            if problems:
                out[eid] = problems
        return {"entries": out}
    except Exception as e:  # noqa: BLE001
        return {"entries": {}, "error": str(e)}
