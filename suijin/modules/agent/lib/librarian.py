"""The Librarian — engagement memory that actually recalls.

The operator contract: a human pentester catalogs interesting things as
they scout, and they pop up INSTANTLY later when a relevant endpoint
appears. The agent found cool things early and forgot them by the time
they mattered. The Librarian is the fix:

  - a daemon thread OWNED by the engagement (redteamer start/finally);
    it never touches the graph — execute_tool_node drops every tool
    result on its desk (observe(), fire-and-forget, never raises)
  - extraction is PATTERN-FIRST (zero LLM): credentials, leaks,
    versions, error oracles, admin/upload surfaces, IDOR worklists,
    footholds, CONFIRMED exploits → a deduped ledger (librarian.json in
    the engagement dir)
  - a RARE LLM digest (every `librarian_interval` batches, default 10)
    condenses fresh observations into <=10 memory lines — the
    supervisor's proven price point (~1 call per 10 turns)
  - RECALL, two ways:
      1. EVERY TURN think_node asks relevant_for_step() — the top
         entries matching the current target/URL/args render in the
         context block (top of its mind, exactly when needed)
      2. ON DEMAND via the memory_recall tool

Everything here is best-effort: a librarian crash is logged and the run
continues untouched.
"""

from __future__ import annotations

import contextlib
import json
import queue
import re
import threading
import time
from pathlib import Path

# ── the singleton lifecycle (module hook — the tools/graph layer never
#    holds a reference; redteamer owns start/stop) ───────────────────────

_ACTIVE: "Librarian | None" = None
_lock = threading.Lock()

# UI publish hook (set by the red-teamer): fn(count). The agent layer
# never imports the red TUI — the hook is wired where the UI lives.
_UI_PUBLISH = None


def set_ui_publish(fn) -> None:
    global _UI_PUBLISH
    _UI_PUBLISH = fn


def active() -> "Librarian | None":
    return _ACTIVE


def start(generate_fn, engagement_dir: Path, interval: int = 10, target: str = "") -> "Librarian | None":
    """Boot the librarian for this engagement (idempotent)."""
    global _ACTIVE
    with _lock:
        if _ACTIVE is not None and _ACTIVE.alive:
            return _ACTIVE
        try:
            _ACTIVE = Librarian(generate_fn, engagement_dir, interval=int(interval or 10), target=target)
            _ACTIVE._start()
            return _ACTIVE
        except Exception:  # noqa: BLE001 — memory must never block a run
            _ACTIVE = None
            return None


def stop() -> None:
    global _ACTIVE
    with _lock:
        if _ACTIVE is not None:
            _ACTIVE._stop()
        _ACTIVE = None


def observe(tool_name: str, tool_args: dict, output: str, iteration: int = 0) -> None:
    """The nerve ending: execute_tool_node drops every result here.
    Fire-and-forget, never raises, no-op when no librarian is active."""
    lb = _ACTIVE
    if lb is None:
        return
    with contextlib.suppress(Exception):  # queue full / shutdown races — drop, never raise
        lb._q.put_nowait((str(tool_name or ""), dict(tool_args or {}), str(output or ""), int(iteration or 0)))


def relevant_for_step(state: dict, tool_args: dict | None = None) -> list[str]:
    """Top-of-mind recall: ledger entries matching the CURRENT step's
    target/URL/args (+the engagement target). Pure, fast, never raises."""
    lb = _ACTIVE
    if lb is None:
        return []
    try:
        args = dict(tool_args or {})
        terms: list[str] = []
        for k in ("url", "target", "host", "path", "endpoint", "domain"):
            v = str(args.get(k) or "").strip()
            if v:
                terms.append(v)
        with contextlib.suppress(Exception):
            from suijin.modules.agent.lib.attack_memory import target_key

            _t = target_key(str((state or {}).get("original_objective") or ""))
            if _t:
                terms.append(_t)
        return lb.relevant(terms)
    except Exception:  # noqa: BLE001
        return []


def _expand_terms(terms: list[str]) -> list[str]:
    """URL → host + path pieces; strings → significant tokens. Deduped,
    ordered (first mentions keep priority)."""
    out: list[str] = []
    for t in terms or []:
        t = str(t).casefold().strip()
        if not t:
            continue
        out.append(t)
        m = re.match(r"https?://([^/\s?]+)", t)
        if m:
            host = m.group(1)
            out.append(host)
            if "." in host:
                out.append(host.split(".", 1)[-1])  # t.local from vault.t.local
            rest = t.split(host, 1)[-1] if host in t else ""
        else:
            rest = t
        for piece in re.split(r"[/\s?=&]+", rest):
            if len(piece) > 4:
                out.append(piece)
    return [t for t in dict.fromkeys(out) if len(t) > 3]


def recall(query: str = "", limit: int = 8) -> str:
    """The memory_recall tool body: query the ledger (kinds, values,
    digests). No librarian → a short honest message."""
    lb = _ACTIVE
    if lb is None:
        return "No engagement memory: the librarian is not running (headless/test)."
    return lb.render_ledger(query=str(query or ""), limit=max(1, min(int(limit or 8), 30)))


# ── extraction patterns (high-signal, zero LLM) ────────────────────────

_PATTERNS: list[tuple[str, re.Pattern, int]] = [
    # kind, regex (group 0 captured), relevance priority
    ("confirmed exploit", re.compile(r"\bEXP-\d+\s+CONFIRMED[^\n]{0,120}"), 9),
    ("foothold", re.compile(r"\buid=\d+\([a-z]+\)[^\n]{0,80}"), 8),
    (
        "credential",
        re.compile(
            r"\b(AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
        ),
        7,
    ),
    ("secret leak", re.compile(r"(?i)\b((?:api|secret|access|private|auth)[_a-z]*key[_a-z]*\s*[=:]\s*\S{8,})"), 6),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), 6),
    ("idor candidate", re.compile(r"(?i)(cross[- ]credential|id fields differ|hidden param\w*)[^\n]{0,80}"), 5),
    (
        "error oracle",
        re.compile(
            r"(?i)(sql syntax|SQLException|stack ?trace|warning: mysql|uncaught \w*exception|debug mode (is )?enabl\w+)"
        ),
        4,
    ),
    ("reflected input", re.compile(r"(?i)(reflected (?:in|detected)|tag survived|marker reflected)[^\n]{0,80}"), 4),
    (
        "version",
        re.compile(
            r"(?i)\b(nginx|apache|wordpress|php|tomcat|jenkins|gitlab|node|express|django|flask|struts|redis|postgres|mysql)[/\- ]v?([0-9][0-9a-z.\-]{1,15})"
        ),
        2,
    ),
    (
        "admin surface",
        re.compile(
            r"(?i)\b(?:https?://[^\s\"'<>]{0,80}|/)[^\s\"'<>]{0,40}/(admin|wp-admin|manager|console|cpanel)[^\s\"'<>]{0,40}"
        ),
        3,
    ),
    (
        "upload surface",
        re.compile(
            r"(?i)\b(?:https?://[^\s\"'<>]{0,80}|/)[^\s\"'<>]{0,40}/(upload|uploads|file|attach)[^\s\"'<>]{0,40}"
        ),
        3,
    ),
]

_KIND_CAP = 40  # per-kind cap keeps one noisy pattern from starving the rest
_LEDGER_CAP = 400


class Librarian:
    def __init__(self, generate_fn, engagement_dir, interval: int = 10, target: str = ""):
        self._gen = generate_fn
        self._dir = Path(engagement_dir)
        self._interval = max(1, int(interval or 10))
        self._target = str(target or "")
        self._q: queue.Queue = queue.Queue(maxsize=2000)
        self._stop_flag = threading.Event()
        self._thread: threading.Thread | None = None
        self.alive = False
        self._ledger: dict = {"entries": [], "digests": []}
        self._since_digest = 0
        self._last_kind_counts: dict = {}
        self._load()

    # ── lifecycle ────────────────────────────────────────────────────

    def _start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="suijin-librarian", daemon=True)
        self.alive = True
        self._thread.start()

    def _stop(self) -> None:
        self._stop_flag.set()
        self.alive = False
        with contextlib.suppress(Exception):
            self._drain_once(final=True)  # flush the queue into the ledger

    def _run(self) -> None:
        _fail_logged = False
        while not self._stop_flag.wait(0.5):
            try:
                self._drain_once()
            except Exception as e:  # noqa: BLE001 — a silently dead librarian is invisible memory loss
                if not _fail_logged:
                    _fail_logged = True
                    with contextlib.suppress(Exception):
                        import logging

                        logging.getLogger("suijin").warning(f"librarian drain failed (once-logged): {e!r}")

    # ── ingestion ────────────────────────────────────────────────────

    def _load(self) -> None:
        with contextlib.suppress(Exception):
            p = self._dir / "librarian.json"
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._ledger = {
                        "entries": list(data.get("entries") or [])[:_LEDGER_CAP],
                        "digests": list(data.get("digests") or [])[-3:],
                    }
        self._publish_count()

    def _persist(self) -> None:
        with contextlib.suppress(Exception):
            self._dir.mkdir(parents=True, exist_ok=True)
            (self._dir / "librarian.json").write_text(json.dumps(self._ledger, indent=2), encoding="utf-8")

    def _publish_count(self) -> None:
        with contextlib.suppress(Exception):
            if _UI_PUBLISH is not None:
                _UI_PUBLISH(len(self._ledger.get("entries") or []))

    def _drain_once(self, final: bool = False) -> None:
        """Consume queued observations: extract → ledger → maybe digest."""
        got = 0
        while True:
            try:
                tool_name, args, output, iteration = self._q.get_nowait()
            except queue.Empty:
                break
            got += 1
            self._extract(tool_name, args, output, iteration)
        if got:
            self._since_digest += got
            self._persist()
            self._publish_count()
        if (self._since_digest >= self._interval or final) and self._gen is not None:
            self._digest(final=final)

    def _extract(self, tool_name: str, args: dict, output: str, iteration: int) -> None:
        text = str(output or "")
        if len(text) > 200_000:
            text = text[:100_000] + text[-100_000:]
        where = (
            " ".join(str(args.get(k) or "") for k in ("url", "target", "host", "path", "cmd") if args.get(k))[:120]
            or tool_name
        )
        entries = self._ledger["entries"]
        seen = {(e.get("kind"), e.get("value")) for e in entries}
        kind_counts = dict(self._last_kind_counts or {})
        for kind, rx, _prio in _PATTERNS:
            for m in rx.finditer(text):
                val = " ".join(str(m.group(0)).split())[:160]
                if not val or (kind, val) in seen:
                    continue
                if kind_counts.get(kind, 0) >= _KIND_CAP:
                    continue
                seen.add((kind, val))
                kind_counts[kind] = kind_counts.get(kind, 0) + 1
                entries.append(
                    {"kind": kind, "value": val, "where": where, "iter": iteration, "ts": time.strftime("%H:%M:%S")}
                )
        # args themselves can carry the surface (a probe against /admin
        # IS the observation)
        with contextlib.suppress(Exception):
            _u = str(args.get("url") or args.get("target") or "")
            if _u:
                for kind, rx, _p in _PATTERNS:
                    if kind in ("admin surface", "upload surface") and rx.search(_u):
                        val = " ".join(_u.split())[:160]
                        if (kind, val) not in seen and kind_counts.get(kind, 0) < _KIND_CAP:
                            seen.add((kind, val))
                            kind_counts[kind] = kind_counts.get(kind, 0) + 1
                            entries.append(
                                {
                                    "kind": kind,
                                    "value": val,
                                    "where": _u[:120],
                                    "iter": iteration,
                                    "ts": time.strftime("%H:%M:%S"),
                                }
                            )
        self._last_kind_counts = kind_counts
        del entries[_LEDGER_CAP:]

    # ── the rare LLM digest ──────────────────────────────────────────

    def _digest(self, final: bool = False) -> None:
        """Condense the fresh observations into <=10 memory lines — ONE
        small call per interval, never blocks, failure skips silently.
        The FINAL drain (engagement end) persists without an LLM call —
        teardown must never wait on a provider."""
        self._since_digest = 0
        if final or self._gen is None:
            return
        fresh = self._ledger["entries"][-40:]
        if not fresh:
            return
        lines = [f"- [{e['kind']}] {e['value']} (iter {e.get('iter', '?')})" for e in fresh]
        try:
            import asyncio

            prompt = (
                "You are the engagement librarian. Condense these penetration-test observations into "
                "AT MOST 10 short imperative memory lines (each: what was found + where). Keep every "
                "credential, foothold, confirmed exploit and reusable fact VERBATIM. Drop noise.\n\n" + "\n".join(lines)
            )

            async def _call():
                return await self._gen(
                    [
                        {"role": "system", "content": "You are a terse memory indexer. Output only the lines."},
                        {"role": "user", "content": prompt},
                    ],
                    {"max_tokens_per_request": 600, "temperature": 0.1},
                )

            out = str(asyncio.run(_call()) or "")
            if out and not out.startswith("Error:"):
                kept = [ln.strip("- ").strip() for ln in out.splitlines() if ln.strip() and len(ln.strip()) > 8][:10]
                if kept:
                    self._ledger["digests"] = (self._ledger.get("digests") or [])[-2:] + [
                        {"ts": time.strftime("%H:%M:%S"), "lines": kept, "final": final}
                    ]
                    self._persist()
        except Exception:  # noqa: BLE001 — the digest is a luxury, never a dependency
            pass

    # ── recall ───────────────────────────────────────────────────────

    _PRIO = {kind: prio for kind, _rx, prio in _PATTERNS}

    def relevant(self, terms: list[str], limit: int = 4) -> list[str]:
        """Entries matching any term, ranked by priority + recency. Terms
        are EXPANDED: a full URL also matches on its host and path pieces
        (the entry was recorded as https://host/x, the step asks about
        https://host/x/DEEP/path — they must still meet)."""
        tl = _expand_terms(terms)
        if not tl:
            return []
        scored = []
        for i, e in enumerate(self._ledger["entries"]):
            hay = f"{e.get('value', '')} {e.get('where', '')}".casefold()
            hits = sum(1 for t in tl if t in hay)
            if not hits:
                continue
            prio = self._PRIO.get(e.get("kind", ""), 1)
            scored.append((prio * 10 + hits * 5 + i / max(1, len(self._ledger["entries"])), e))
        scored.sort(key=lambda se: -se[0])
        out = []
        for _s, e in scored[:limit]:
            out.append(
                f"[{e['kind']}] {e['value']}" + (f"  (found at {e.get('where', '')[:60]})" if e.get("where") else "")
            )
        return out

    def render_ledger(self, query: str = "", limit: int = 8) -> str:
        """The memory_recall tool body."""
        digests = self._ledger.get("digests") or []
        head = [f"Engagement memory: {len(self._ledger['entries'])} observations"]
        for d in digests[-1:]:
            head.append("Latest condensed digest (" + str(d.get("ts", "")) + "):")
            head.extend("  - " + ln for ln in (d.get("lines") or [])[:10])
        q = query.strip().casefold()
        entries = self._ledger["entries"]
        if q:
            entries = [
                e for e in entries if q in f"{e.get('kind', '')} {e.get('value', '')} {e.get('where', '')}".casefold()
            ]
        for e in entries[-limit:]:
            head.append(f"- [{e['kind']}] {e['value']}  (iter {e.get('iter', '?')}, {e.get('where', '')[:60]})")
        if len(entries) > limit:
            head.append(f"… {len(entries) - limit} more — memory_recall(query=…, limit=30)")
        return "\n".join(head) if len(head) > 1 else head[0]
