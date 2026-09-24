"""The logging worker — server-contained, off-loop, writing the human
readable per-engagement log.

The runner's event bus feeds this worker; it drains records into
``engagements/<id>/log/engagement.log`` — one readable line per record.
Off the engagement's event loop (a thread today; the executor stage
makes it a process). If it dies, the run continues: the durable truth
is events.jsonl, not this file.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import queue as _q
import threading


def _fmt(rec: dict) -> str:
    ts = _dt.datetime.now().strftime("%H:%M:%S")
    k = rec.get("kind", "?")
    if k == "iteration":
        return f"{ts} iter {rec.get('n')} · {rec.get('phase')}"
    if k == "tool.call":
        return f"{ts} → {rec.get('name')} {str(rec.get('args'))[:160]}"
    if k == "tool.result":
        status = "ok" if rec.get("ok") else f"FAIL({rec.get('error_kind')})"
        out = str(rec.get("output") or "").strip().replace("\n", " ")[:120]
        return f"{ts} ← {status} {rec.get('duration_ms')}ms {out}"
    if k == "assistant.message":
        return f"{ts} decision {str(rec.get('content'))[:140]}"
    if k == "phase.transition":
        return f"{ts} phase {rec.get('from')} → {rec.get('to')}"
    if k == "guidance.delivered":
        return f"{ts} guidance: {str(rec.get('text'))[:160]}"
    if k == "usage":
        return f"{ts} usage in={rec.get('input_tokens')} out={rec.get('output_tokens')} ${rec.get('cost_usd')}"
    if k in ("session.complete", "session.interrupt"):
        return f"{ts} {k}: {str(rec.get('reason'))[:160]}"
    if k == "session.start":
        return f"{ts} START {str(rec.get('objective'))[:120]}"
    return f"{ts} {k} {str(rec)[:160]}"


class LogWorker:
    """Subscribes to the bus via a queue pump; writes the log file."""

    def __init__(self, bus, log_path):
        from pathlib import Path

        self._q: _q.Queue = _q.Queue(maxsize=2000)
        self._path = Path(log_path)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pump = lambda rec: self._enqueue(rec)
        bus.subscribe(self._pump)

    def _enqueue(self, rec: dict) -> None:
        with contextlib.suppress(_q.Full):
            self._q.put_nowait(rec)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="suijin-log")
        self._thread.start()

    def _run(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            while not self._stop.wait(0.05):
                drained = False
                while True:
                    try:
                        rec = self._q.get_nowait()
                    except _q.Empty:
                        break
                    drained = True
                    with contextlib.suppress(OSError):
                        fh.write(_fmt(rec) + "\n")
                if drained:
                    with contextlib.suppress(OSError):
                        fh.flush()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
