"""Evidence bundle exporter — chain-of-custody zip for engagement artifacts.

`suijin export` packs everything an engagement produced into one zip with a
SHA-256 manifest: reports, audit trails, sessions, blue state, dossiers,
credentials (opt-in), both knowledge graphs, and the redacted config.
Pure stdlib (zipfile + hashlib) — no API keys, fully offline.
"""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from suijin import __version__ as VERSION
from suijin.core.constants import BLUE_KG_PATH
from suijin.tools.workspace import WORKSPACE_DIR

# workspace-relative folders always included when present
_BUNDLE_DIRS = (
    "reports",
    "audit_trails",
    "sessions",
    "blue_state",
    "dossiers",
    "evidence",
    "evidence_chains",
)
_SECRET_MARKERS = ("key", "token", "secret", "password", "credential")
_MAX_FILE_BYTES = 50 * 1024 * 1024  # refuse monsters (offloaded outputs etc.)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _redact(obj):
    if isinstance(obj, dict):
        return {
            k: ("***redacted***" if any(m in k.lower() for m in _SECRET_MARKERS) and v else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=WORKSPACE_DIR.parent,
        ).stdout.strip()
    except Exception:
        return ""


def build_bundle(
    out_path: Path | None = None,
    include_credentials: bool = False,
    workspace: Path | None = None,
    red_kg_path: Path | None = None,
    blue_kg_path: Path | None = None,
    config_path: Path | None = None,
) -> Path:
    """Create the evidence bundle zip. Returns its path.

    Structure:
      manifest.json        SHA-256 + size for every file, in bundle order
      custody.json         who/when/where/how metadata (chain of custody)
      config.redacted.json effective red config with secrets redacted
      workspace/...        reports, audits, sessions, blue state, ...
      kg/blue.json         blue session knowledge graph (if a session ran)
      kg/red.json          persistent red constraints graph
    """
    ws = Path(workspace) if workspace else WORKSPACE_DIR
    red_kg = Path(red_kg_path) if red_kg_path else ws.parent / "suijin" / "intel" / "knowledge_graph.json"
    blue_kg = Path(blue_kg_path) if blue_kg_path else Path(BLUE_KG_PATH)
    cfg = Path(config_path) if config_path else ws.parent / "suijin" / "config.json"

    ts = datetime.now(timezone.utc)
    if out_path is None:
        out_dir = ws / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"suijin_bundle_{ts.strftime('%Y%m%d_%H%M%S')}.zip"

    manifest: list[dict] = []

    with ZipFile(out_path, "w", ZIP_DEFLATED) as zf:

        def add_file(src: Path, arcname: str):
            if not src.is_file() or src.stat().st_size > _MAX_FILE_BYTES:
                return
            zf.write(src, arcname)
            manifest.append(
                {
                    "path": arcname,
                    "sha256": _sha256(src),
                    "bytes": src.stat().st_size,
                }
            )

        # workspace artifacts
        for folder in _BUNDLE_DIRS:
            d = ws / folder
            if not d.is_dir():
                continue
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    add_file(f, f"workspace/{f.relative_to(ws)}")
        if include_credentials and (ws / "credentials.json").is_file():
            add_file(ws / "credentials.json", "workspace/credentials.json")

        # knowledge graphs
        add_file(blue_kg, "kg/blue.json")
        add_file(red_kg, "kg/red.json")

        # redacted config (tracked in the manifest like every other file)
        if cfg.is_file():
            try:
                data = _redact(json.loads(cfg.read_text()))
            except ValueError:
                data = {"error": "config.json unreadable"}
            blob = json.dumps(data, indent=2, sort_keys=True).encode()
            zf.writestr("config.redacted.json", blob)
            manifest.append(
                {
                    "path": "config.redacted.json",
                    "sha256": hashlib.sha256(blob).hexdigest(),
                    "bytes": len(blob),
                }
            )

        # chain of custody
        custody = {
            "created_at": ts.isoformat(),
            "tool": f"suijin {VERSION}",
            "created_by": _git_head() or "unknown-commit",
            "host": socket.gethostname(),
            "workspace": str(ws),
            "file_count": len(manifest),
            "include_credentials": include_credentials,
            "note": "SHA-256 hashes in manifest.json cover every bundled "
            "file except manifest.json and custody.json themselves.",
        }
        zf.writestr("custody.json", json.dumps(custody, indent=2))
        zf.writestr("manifest.json", json.dumps({"algorithm": "sha256", "files": manifest}, indent=2))

    return out_path


def verify_bundle(zip_path: Path) -> tuple[bool, list[str]]:
    """Re-hash every manifest entry; returns (ok, problems)."""
    problems: list[str] = []
    with ZipFile(zip_path) as zf:
        try:
            mani = json.loads(zf.read("manifest.json"))
        except KeyError:
            return False, ["manifest.json missing"]
        for entry in mani.get("files", []):
            name = entry["path"]
            try:
                data = zf.read(name)
            except KeyError:
                problems.append(f"{name}: listed but absent")
                continue
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                problems.append(f"{name}: hash mismatch")
            if len(data) != entry["bytes"]:
                problems.append(f"{name}: size mismatch")
        # unlisted files (beyond the manifest/custody themselves) are a finding
        listed = {e["path"] for e in mani.get("files", [])} | {"manifest.json", "custody.json"}
        for name in zf.namelist():
            if name not in listed:
                problems.append(f"{name}: in bundle but not in manifest")
    return (not problems), problems
