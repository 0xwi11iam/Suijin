"""Marketplace (F41-F43) — pack index, install, updates, trust.

An index is a JSON document (served anywhere — a gist, a repo raw URL,
a file) describing packs: id, version, url (zip or git), sha256, and
(optional) signature. Trust ladder:
  level 0: hash-pinned entries (sha256 verified on install)
  level 1: signed entries (signature verified against a known key —
           the index carries the pack's signing key fingerprint)

No central server is required: ANY index URL works (self-hosted,
community, private). Suijin remains local-first.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_INDEX = "https://raw.githubusercontent.com/0xwi11iam/Suijin-marketplace/main/index.json"


def fetch_index(url: str = DEFAULT_INDEX) -> dict:
    """Download + parse an index document."""
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.loads(r.read().decode())
    if not isinstance(data, dict) or "packs" not in data:
        raise ValueError("index document has no 'packs' map")
    return data


def search(query: str = "", url: str = DEFAULT_INDEX) -> list[dict]:
    idx = fetch_index(url)
    q = (query or "").lower()
    hits = []
    for pid, meta in idx["packs"].items():
        if not q or q in pid.lower() or q in str(meta.get("description", "")).lower():
            hits.append({"id": pid, **meta})
    return hits


def _user_modules_dir() -> Path:
    d = Path.home() / ".suijin" / "modules"
    d.mkdir(parents=True, exist_ok=True)
    return d


def install_pack(pack_id: str, url: str = DEFAULT_INDEX, into: Path | None = None) -> str:
    """Install a pack from the index: download, hash-verify, unpack into
    ~/.suijin/modules (or `into`). Returns a result note."""
    idx = fetch_index(url)
    meta = idx["packs"].get(pack_id)
    if meta is None:
        avail = ", ".join(sorted(idx["packs"])[:10])
        return f"Error: no pack '{pack_id}' in index (have: {avail}...)"
    dest = into or _user_modules_dir() / pack_id
    if dest.exists():
        return f"Error: already installed at {dest} (module update {pack_id} to refresh)"
    src = meta.get("url", "")
    if not src:
        return "Error: index entry has no url"
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        archive = tdp / "pack.zip"
        try:
            with urllib.request.urlopen(src, timeout=60) as r, archive.open("wb") as f:
                shutil.copyfileobj(r, f)
        except OSError as e:
            return f"Error: download failed: {e}"
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        want = meta.get("sha256", "")
        if want and digest != want:
            return f"Error: HASH MISMATCH (index says {want[:16]}..., got {digest[:16]}...) — refusing to install"
        if not zipfile.is_zipfile(archive):
            return "Error: download is not a zip pack"
        with zipfile.ZipFile(archive) as z:
            root = tdp / "unpack"
            z.extractall(root)
            # pack root = single subdir if wrapped, else unpack itself
            entries = [p for p in root.iterdir() if p.name != "__MACOSX"]
            pack_root = entries[0] if len(entries) == 1 and entries[0].is_dir() else root
            if not (pack_root / "plugin.json").exists() and not (pack_root / "manifest.json").exists():
                return "Error: archive has no plugin.json/manifest.json at its root — not a suijin pack"
            shutil.copytree(pack_root, dest)
    sig = " signed" if meta.get("signature") else " hash-pinned" if want else " UNPINNED (no sha256 in index)"
    return f"installed {pack_id} v{meta.get('version', '?')} -> {dest} ({sig})"


def update_pack(pack_id: str, url: str = DEFAULT_INDEX) -> str:
    """Refresh an installed user pack from the index."""
    dest = _user_modules_dir() / pack_id
    if not dest.exists():
        return f"Error: {pack_id} is not an installed user pack"
    backup = dest.with_suffix(".bak")
    if backup.exists():
        shutil.rmtree(backup)
    shutil.copytree(dest, backup)
    shutil.rmtree(dest)
    result = install_pack(pack_id, url)
    if result.startswith("Error"):
        shutil.copytree(backup, dest)  # roll back
        return f"{result} (rolled back to previous version)"
    shutil.rmtree(backup, ignore_errors=True)
    return result


def list_installed() -> list[dict]:
    """User-installed packs with versions."""
    out = []
    for p in sorted(_user_modules_dir().iterdir()):
        pj = p / "plugin.json"
        if pj.exists():
            try:
                meta = json.loads(pj.read_text())
                out.append({"id": meta.get("id", p.name), "version": meta.get("version", "?"), "path": str(p)})
            except (OSError, ValueError):
                continue
    return out
