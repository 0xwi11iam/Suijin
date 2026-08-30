"""Engagement bundles — .sje (Suijin Engagement) save/resume.

The checkpointer is MemorySaver: engagement state DIES with the process.
The .sje bundle makes a concluded engagement portable and RESUMABLE:

  save (automatic at conclusion):
    outputs/exports/<name>.sje — a zip containing
      manifest.json      objective, thread, config (keys stripped), cost,
                         per-file sha256 (tamper-evident)
      graph_state.json   the resume subset of the final graph state
                         (messages, traces, chain memory, phase, todos…)
      exploits/**        the engagement's exploit catalog (POCs + receipts)
      notes.md           the engagement's note log, when present

  load (`suijin load <file.sje>`):
    verifies every hash, restores the exploit catalog + notes into the
    workspace, then resumes the engagement — the saved state is injected
    into a fresh graph thread (the same update_state seam operator
    guidance uses), so the agent CONTINUES with full memory: it knows
    what it tried, what failed, what was proven.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SJE_VERSION = 1

# the resume subset of AgentState (state.py) — memory that compounds.
# completion_reason is deliberately NOT here: a resumed engagement must
# run, not instantly re-complete.
RESUME_KEYS = (
    "messages",
    "original_objective",
    "conversation_objectives",
    "current_objective_index",
    "objective_history",
    "current_phase",
    "phase_history",
    "attack_path_type",
    "current_iteration",
    "execution_trace",
    "todo_list",
    "target_info",
    "chain_findings_memory",
    "chain_failures_memory",
    "chain_decisions_memory",
    "chain_waves_memory",
    "tested_axes",
    "qa_history",
    "_prompt_profile",
)
MAX_RESUME_MESSAGES = 80  # the freshest context; the older tail is noise
MAX_RESUME_TRACE = 150
_SENSITIVE = ("key", "token", "secret", "password")


def _exports_dir() -> Path:
    from suijin.modules.platform.lib.workspace import artifact_dir  # function-local (boundary law)

    d = artifact_dir("exports")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _engagement_slug(objective: str) -> str:
    words = re.sub(r"[^a-zA-Z0-9 ]", " ", str(objective or "engagement")).split()
    return ("_".join(w.lower() for w in words[:4]) or "engagement")[:48]


def _sanitize_config(config: dict) -> dict:
    out = {}
    for k, v in dict(config or {}).items():
        if any(s in str(k).lower() for s in _SENSITIVE):
            out[k] = "***stripped***"
        else:
            out[k] = v
    return out


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exploits_root() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR / "exploits"


def save_engagement(thread_id: str, objective: str, config: dict, state: dict, cost: float = 0.0) -> Path:
    """Bundle the concluded engagement. Never raises into the caller's flow."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name = f"{_engagement_slug(objective)}_{ts}.sje"
    path = _exports_dir() / name

    graph_state = {}
    for k in RESUME_KEYS:
        if k in state:
            graph_state[k] = state[k]
    msgs = graph_state.get("messages") or []
    if len(msgs) > MAX_RESUME_MESSAGES:
        keep = msgs[-MAX_RESUME_MESSAGES:]
        keep[0] = dict(keep[0])
        keep[0]["content"] = (
            "(context resumed from a saved engagement — older turns trimmed)\n" + str(keep[0].get("content", ""))[:200]
        )
        graph_state["messages"] = keep
    trace = graph_state.get("execution_trace") or []
    if len(trace) > MAX_RESUME_TRACE:
        graph_state["execution_trace"] = trace[-MAX_RESUME_TRACE:]

    manifest = {
        "format": "sje",
        "version": SJE_VERSION,
        "thread_id": thread_id,
        "objective": str(objective or ""),
        "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "config": _sanitize_config(config),
        "cost_usd": float(cost or 0.0),
        "files": {},
    }

    payload = {"manifest.json": json.dumps(manifest, indent=2, default=str).encode()}
    payload["graph_state.json"] = json.dumps(graph_state, indent=2, default=str).encode()

    # the exploit catalogs (POCs, receipts — the proof). The whole root
    # travels: per-engagement subdirs are small, and the agent names the
    # engagement string freely (target names, slugs) — exact-match guessing
    # would silently drop the proof.
    edir = _exploits_root()
    if edir.is_dir():
        for f in sorted(edir.rglob("*")):
            if f.is_file() and f.stat().st_size <= 2_000_000:
                rel = f"exploits/{f.relative_to(edir)}"
                try:
                    payload[rel] = f.read_bytes()
                except OSError:
                    continue

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        # the manifest cannot hash itself — the seal covers every PAYLOAD
        # file; manifest integrity follows from those hashes
        for rel, data in payload.items():
            if rel != "manifest.json":
                manifest["files"][rel] = _sha256(data)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
        for rel, data in payload.items():
            if rel != "manifest.json":
                zf.writestr(rel, data)
    return path


def load_engagement(path: str | Path) -> dict:
    """Open + hash-verify a .sje. Returns {manifest, graph_state} — or
    raises ValueError with the exact tamper/broken reason."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"no such file: {p}")
    if p.suffix != ".sje":
        raise ValueError(f"not a .sje engagement bundle: {p.name}")
    try:
        with zipfile.ZipFile(p) as zf:
            names = zf.namelist()
            if "manifest.json" not in names or "graph_state.json" not in names:
                raise ValueError("bundle is missing manifest.json/graph_state.json — not a valid .sje")
            manifest = json.loads(zf.read("manifest.json"))
            if manifest.get("format") != "sje":
                raise ValueError("manifest is not an sje bundle")
            for rel, want in (manifest.get("files") or {}).items():
                if rel not in names:
                    raise ValueError(f"sealed file missing from bundle: {rel}")
                if _sha256(zf.read(rel)) != want:
                    raise ValueError(f"hash mismatch for {rel} — bundle was tampered with or corrupted")
            graph_state = json.loads(zf.read("graph_state.json"))
    except zipfile.BadZipFile as e:
        raise ValueError(f"corrupt bundle: {e}") from e
    return {"manifest": manifest, "graph_state": graph_state}


def restore_side_files(path: str | Path) -> int:
    """Put the bundled exploit catalogs back into the workspace.
    Returns files restored."""
    p = Path(path)
    dest = _exploits_root()
    n = 0
    with zipfile.ZipFile(p) as zf:
        for name in zf.namelist():
            if name.startswith("exploits/") and not name.endswith("/"):
                rel = name[len("exploits/") :]
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))
                n += 1
    return n


def resume_engagement(path: str | Path) -> int:
    """Verify, restore, and CONTINUE a saved engagement in the live TUI."""
    bundle = load_engagement(path)
    manifest = bundle["manifest"]
    objective = manifest.get("objective", "")
    restore_side_files(path)
    # CURRENT config wins over the bundle's stale copy. The bundle's config
    # froze the operator intent of a past session (provider, models, caps) —
    # resuming with it silently ignored provider switches made AFTER the
    # bundle was saved (field incident: zai set, deepseek ran, 402 death).
    # Engagement STATE rides the bundle; operator SETTINGS ride config.json.
    from suijin.modules.platform.lib.config_loader import load_config

    config = {**dict(manifest.get("config") or {}), **load_config()}
    graph_state = dict(bundle["graph_state"] or {})
    graph_state["completion_reason"] = None  # resumed = keep working

    from suijin.modules.redteam.lib.redteamer import run_red_team

    return int(run_red_team(config, objective, resume_state=graph_state) or 0)
