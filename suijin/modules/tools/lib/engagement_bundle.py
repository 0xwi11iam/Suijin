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

import atexit
import contextlib
import hashlib
import json
import os
import re
import signal
import sys
import threading
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
    "tested_axes",
    "findings",
    "_attack_queue",
    "_foothold_at",
    "_prompt_profile",
)
MAX_RESUME_MESSAGES = 80  # the freshest context; the older tail is noise
MAX_RESUME_TRACE = 150
_SENSITIVE = ("key", "token", "secret", "password")


def _exports_dir() -> Path:
    """Where bundles live: INSIDE the engagement (v3 — the folder stays in
    place, so the bundle survives with it). Falls back to the legacy
    workspace exports/ for pre-v3 workspaces when no engagement is
    active (the picker reads both)."""
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR, engagement_dir  # function-local (boundary law)

    eng = engagement_dir()
    d = eng / "state" if eng.parent.name != "_default" else WORKSPACE_DIR / "exports"
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
    """The 2026-09-16 layout keeps catalogs inside engagements — harvest
    the CURRENT engagement's exploits (plus the legacy flat root when an
    old workspace is resumed)."""
    from suijin.modules.platform.lib.workspace import exploits_dir

    return exploits_dir()


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

    # engagement memory rides the bundle (wave: session memory): the
    # librarian ledger + the scratchpad — without these, a resume forgot
    # every observation and re-paid the recon tokens
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.workspace import state_dir as _sdir

        # 2026-09-16 layout: state files live in engagements/<slug>/state/
        # (the restructure moved them out of the engagement root — the old
        # paths silently missed, so resumes forgot every observation)
        _led = _sdir() / "librarian.json"
        if _led.is_file():
            graph_state["_librarian_ledger"] = json.loads(_led.read_text(encoding="utf-8"))
        _sp = _sdir() / "scratchpad.md"
        if _sp.is_file():
            graph_state["_scratchpad_text"] = _sp.read_text(encoding="utf-8")[-8000:]

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

    # ATOMIC write: a KI mid-zip left a truncated bundle on disk (silently
    # corrupt). Build beside the target, then os.replace — readers never
    # see a half-written .sje. Stale tmps from crashes get swept here.
    tmp_path = path.with_suffix(".sje.tmp")
    with contextlib.suppress(Exception):
        import os as _os2
        import time as _time2

        for stale in _exports_dir().glob("*.sje.tmp"):
            if _time2.time() - stale.stat().st_mtime > 3600:
                _os2.unlink(stale)
    tmp_path = path.with_suffix(".sje.tmp")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # the manifest cannot hash itself — the seal covers every PAYLOAD
        # file; manifest integrity follows from those hashes
        for rel, data in payload.items():
            if rel != "manifest.json":
                manifest["files"][rel] = _sha256(data)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
        for rel, data in payload.items():
            if rel != "manifest.json":
                zf.writestr(rel, data)
    import os as _os

    _os.replace(tmp_path, path)
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
    """Verify, restore, and CONTINUE a saved engagement in the live TUI.
    Accepts a path OR a bare bundle name from the exports inbox."""
    path = resolve_bundle(path)
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

    # stripped placeholders ("***stripped***") are not settings — a stale
    # marker could override a good default for any key the operator's file
    # does not pin. Drop them; the live config fills the hole.
    saved_cfg = {k: v for k, v in dict(manifest.get("config") or {}).items() if v != "***stripped***"}
    config = {**saved_cfg, **load_config()}
    graph_state = dict(bundle["graph_state"] or {})
    graph_state["completion_reason"] = None  # resumed = keep working

    from suijin.modules.redteam.lib.redteamer import run_red_team

    return int(run_red_team(config, objective, resume_state=graph_state) or 0)


#  Recent bundles + interactive picker — nobody memorizes timestamped
#  filenames; `suijin load` with no argument lists the ten newest.


def _bundle_files() -> list[Path]:
    """All .sje on disk: v3 engagement state dirs first (the current one's
    bundles excluded), then the legacy workspace exports inbox."""
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    out: dict[str, Path] = {}
    engs = WORKSPACE_DIR / "engagements"
    if engs.is_dir():
        for f in sorted(engs.glob("*/state/*.sje")):
            out.setdefault(str(f), f)
    legacy = WORKSPACE_DIR / "exports"
    if legacy.is_dir():
        for f in sorted(legacy.glob("*.sje")):
            out.setdefault(str(f), f)
    return list(out.values())


def recent_bundles(limit: int = 10) -> list[dict]:
    """The newest .sje bundles (newest first) with display metadata read
    straight from each manifest. Never raises; unreadable bundles still
    list (name/size/date only)."""
    out: list[dict] = []
    with contextlib.suppress(Exception):
        files = [p for p in _bundle_files() if p.is_file()]
        for p in sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)[: max(1, limit)]:
            st = p.stat()
            meta = {
                "path": p,
                "name": p.name,
                "saved_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "size_kb": max(1, st.st_size // 1024),
                "objective": "",
                "cost_usd": 0.0,
                "iterations": None,
            }
            with contextlib.suppress(Exception):
                with zipfile.ZipFile(p) as zf:
                    man = json.loads(zf.read("manifest.json"))
                meta["objective"] = str(man.get("objective", ""))[:78]
                meta["cost_usd"] = float(man.get("cost_usd") or 0.0)
                meta["iterations"] = man.get("iterations")
            out.append(meta)
    return out


def resolve_bundle(ref: str | Path) -> Path:
    """Accept an absolute/relative path OR a bare name (with or without the
    .sje suffix) and resolve it against the exports dir. Raises ValueError
    with the exact reason when nothing matches."""
    raw = str(ref or "").strip().strip("'\"")
    if not raw:
        raise ValueError("no bundle given — run `suijin load` to pick from the recent ten")
    p = Path(raw).expanduser()
    if p.is_file():
        if p.suffix != ".sje":
            raise ValueError(f"not a .sje engagement bundle: {p.name}")
        return p
    if p.is_absolute():
        raise ValueError(f"no such file: {p}")
    # bare name: try the exports inbox (with, then without, the suffix)
    for cand in (raw if raw.endswith(".sje") else f"{raw}.sje", raw):
        q = _exports_dir() / cand
        if q.is_file():
            return q
    raise ValueError(f"no such bundle: {raw} (checked {Path.cwd()} and the exports inbox)")


def pick_bundle(limit: int = 10) -> Path | None:
    """Interactive picker: the ten newest .sje bundles, newest first.
    Returns the chosen Path (or None to abort). Non-TTY falls back to the
    newest bundle automatically (CI resume semantics)."""
    bundles = recent_bundles(limit)
    if not bundles:
        print("no saved engagements yet — nothing to resume")
        return None
    if not sys.stdin.isatty():
        print(f"non-interactive: resuming newest bundle {bundles[0]['name']}")
        return bundles[0]["path"]
    print()
    print("  Recent engagements (newest first):")
    for i, b in enumerate(bundles, 1):
        cost = f" ${b['cost_usd']:.2f}" if b["cost_usd"] else ""
        print(f"   {i:>2}. {b['saved_at']}  {b['size_kb']:>5}KB{cost}  {b['objective'] or b['name']}")
    print()
    try:
        raw = input("  resume which [1-10, or a path/name, blank=1, q=quit]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None
    if not raw:
        return bundles[0]["path"]
    if raw.lower() in ("q", "quit", "n"):
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(bundles):
        return bundles[int(raw) - 1]["path"]
    with contextlib.suppress(ValueError):
        return resolve_bundle(raw)
    print(f"  no match for: {raw}")
    return None


#  CrashSaver — an .sje exists for EVERY exit path
#
#  The loop's exception handler and the final-report block cover normal
#  crashes, but the final block itself can die (an unprotected write, a
#  KeyboardInterrupt during teardown) and everything before the loop sits
#  outside any try. The saver arms at engagement start and guarantees ONE
#  bundle per engagement from: the conclusion path, the finally backstop,
#  SIGTERM/SIGHUP, sys.excepthook, and atexit.


class CrashSaver:
    """Idempotent emergency .sje writer. save() runs at most once per arm();
    the conclusion path marks the save so the backstops become no-ops."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._armed = False
        self._saved = False
        self._thread_id = ""
        self._objective = ""
        self._config: dict = {}
        self._get_state = None
        self._orig_handlers: dict = {}
        self._orig_hook = None
        self.last_path: Path | None = None

    # -- lifecycle -----------------------------------------------------
    def arm(self, thread_id: str, objective: str, config: dict, get_state) -> None:
        """Arm for a live engagement. Re-arming (a second engagement in the
        same process) resets the once-flag."""
        with self._lock:
            self._disarm_locked()
            self._armed = True
            self._saved = False
            self._thread_id = str(thread_id or "")
            self._objective = str(objective or "")
            self._config = dict(config or {})
            self._get_state = get_state
        # process-level backstops: interpreter exit, uncaught exception,
        # termination signals. Registered per-arm; disarm restores.
        with contextlib.suppress(Exception):
            atexit.register(self.save, "atexit")
        for sig, name in ((signal.SIGTERM, "SIGTERM"), (getattr(signal, "SIGHUP", None), "SIGHUP")):
            if sig is None:
                continue
            with contextlib.suppress(Exception):
                prev = signal.getsignal(sig)
                self._orig_handlers[sig] = prev
                signal.signal(sig, lambda s, f, _n=name: self._on_signal(_n, s, f))

    def disarm(self) -> None:
        with self._lock:
            self._disarm_locked()

    def _disarm_locked(self) -> None:
        for sig, prev in self._orig_handlers.items():
            with contextlib.suppress(Exception):
                signal.signal(sig, prev)
        self._orig_handlers = {}
        if self._orig_hook is not None:
            with contextlib.suppress(Exception):
                sys.excepthook = self._orig_hook
            self._orig_hook = None
        self._armed = False

    # -- save ----------------------------------------------------------
    @property
    def saved(self) -> bool:
        return self._saved

    def mark_saved(self) -> None:
        """The conclusion path already wrote the bundle — backstops no-op."""
        self._saved = True

    def save(self, reason: str = "crash") -> Path | None:
        """Write the bundle if armed and unsaved. Never raises (signal
        handlers and atexit must not explode)."""
        with self._lock:
            if not self._armed or self._saved or self._get_state is None:
                return None
            self._saved = True  # claim FIRST: a crash inside the save must
            # not re-enter on the next backstop
        path = None
        try:
            state = {}
            with contextlib.suppress(Exception):
                state = dict(self._get_state() or {})
            path = save_engagement(self._thread_id, self._objective, self._config, state, 0.0)
            self.last_path = path
            if reason != "conclusion":  # the conclusion path prints its own line upstairs
                with contextlib.suppress(Exception):
                    print(f"[sje] engagement saved ({reason}) — resume: suijin load {path.name}", file=sys.stderr)
        except Exception:  # noqa: BLE001 — the emergency save must never raise
            path = None
        return path

    # -- backstops -----------------------------------------------------
    def _on_signal(self, name: str, sig, frame) -> None:
        self.save(f"signal:{name}")
        prev = self._orig_handlers.get(sig)
        if callable(prev):
            prev(sig, frame)  # FULL-AUTO SIGTERM raises KI → pause/quit path
            return
        with contextlib.suppress(Exception):
            signal.signal(sig, signal.SIG_DFL)
            os.kill(os.getpid(), sig)

    def hook_excepthook(self) -> None:
        """Install the uncaught-exception backstop (called at arm time by
        the runner; kept separate so tests can opt in)."""
        with contextlib.suppress(Exception):
            if self._orig_hook is None:
                self._orig_hook = sys.excepthook
                sys.excepthook = self._on_excepthook

    def _on_excepthook(self, tp, val, tb) -> None:
        self.save("excepthook")
        hook = self._orig_hook or sys.__excepthook__
        with contextlib.suppress(Exception):
            hook(tp, val, tb)


CRASH_SAVER = CrashSaver()
