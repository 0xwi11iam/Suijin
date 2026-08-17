"""Flask backend for the Medusa WebUI. See medusa/ui/__init__.py."""

from __future__ import annotations

import contextlib
import json
import os
import queue
import re
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from medusa import __version__ as VERSION
from medusa.core.constants import (
    BLUE_KG_PATH,
    BLUE_TARPIT_FILE,
    BLUE_TRAFFIC_LOG,
)

UI_DIR = Path(__file__).resolve().parent
DIST_DIR = UI_DIR / "dist"
PKG_DIR = UI_DIR.parent  # medusa/ package root

_SECRET_MARKERS = ("key", "token", "secret", "password", "credential")


def _redact(obj):
    if isinstance(obj, dict):
        return {
            k: ("***redacted***" if any(m in k.lower() for m in _SECRET_MARKERS) and v else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _tail_jsonl(path: Path, n: int = 200) -> list[dict]:
    out: list[dict] = []
    try:
        with open(path, "rb") as f:
            try:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 512 * 1024))  # last 512 KB max
                lines = f.read().decode("utf-8", "ignore").splitlines()
            except OSError:
                lines = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return _enrich_traffic(out)


def _enrich_traffic(entries: list[dict]) -> list[dict]:
    """Score raw lab log entries with the blue team's real anomaly detector so
    the dashboard tiers (NORMAL/ANOMALOUS/INVESTIGATED) match the TUI."""
    try:
        from medusa.core.blue.traffic.anomaly_detector import detect_anomalies

        for e in entries:
            try:
                signals = detect_anomalies(e, {"methods": {e.get("method", "GET"): 1}})
                e["ui_score"] = sum(s[1] for s in signals)
                e["ui_signals"] = [s[0] for s in signals]
            except Exception:
                e.setdefault("ui_score", 0)
                e.setdefault("ui_signals", [])
    except Exception:
        for e in entries:
            e.setdefault("ui_score", 0)
            e.setdefault("ui_signals", [])
    return entries


def _workspace() -> Path:
    from medusa.tools.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR


def _safe_workspace_file(rel: str) -> Path | None:
    """Resolve a workspace-relative path, refusing escapes."""
    try:
        base = _workspace().resolve()
        p = (base / rel).resolve()
        p.relative_to(base)
        return p if p.is_file() else None
    except (OSError, ValueError):
        return None


# ── Snapshot builder (shared by /api/overview and the SSE loop) ────────


def build_snapshot() -> dict:
    snap: dict = {"ts": time.time(), "version": VERSION}

    # provider / config
    try:
        cfg = _read_json(PKG_DIR / "config.json", {})
        provider = cfg.get("provider", "deepseek")
        snap["provider"] = {
            "name": provider,
            "model": cfg.get(f"{provider}_model", ""),
            "zai_endpoint": cfg.get("zai_endpoint", "coding") if provider == "zai" else None,
        }
    except Exception:
        snap["provider"] = {"name": "?", "model": "", "zai_endpoint": None}

    # knowledge base
    try:
        from medusa.kb import kb_status

        st = kb_status()
        if st:
            snap["kb"] = {
                "built": True,
                "docs": st["docs"],
                "sources": st["per_source"],
                "failed": st.get("failed", {}),
                "age_days": st.get("age_days"),
                "built_at": st.get("built_at", ""),
            }
        else:
            snap["kb"] = {"built": False}
    except Exception as e:
        snap["kb"] = {"built": False, "error": str(e)}

    # modules / tools
    try:
        from medusa.modules.loader import discover_modules, get_module_tools
        from medusa.tools.availability import missing_binaries

        discover_modules()
        snap["tools"] = {
            "module_tool_count": len(get_module_tools()),
            "missing": {k: v for k, v in missing_binaries().items()},
        }
    except Exception:
        snap["tools"] = {"module_tool_count": 0, "missing": {}}

    # labs + liveness
    labs = []
    lab_dir = PKG_DIR / "lab"
    for d in sorted(lab_dir.iterdir()) if lab_dir.exists() else []:
        if not d.is_dir() or d.name.startswith("__"):
            continue
        app = next((c for c in ("app.py", "vulnerable_app.py") if (d / c).exists()), None)
        if not app:
            continue
        port = _lab_port(d / app)
        running = False
        if port:
            import socket

            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.3)
                    running = s.connect_ex(("127.0.0.1", port)) == 0
            except OSError:
                running = False
        labs.append({"name": d.name, "port": port, "running": running})
    snap["labs"] = labs

    # blue team live state
    snap["tarpit"] = _read_json(Path(BLUE_TARPIT_FILE), {})
    traffic = _tail_jsonl(Path(BLUE_TRAFFIC_LOG), 200)
    snap["traffic_count"] = len(traffic)
    snap["traffic_recent"] = traffic[-25:]
    kg = _read_json(Path(BLUE_KG_PATH), None)
    if kg:
        nodes = kg.get("nodes", {})
        by_type: dict[str, int] = {}
        for n in nodes.values():
            by_type[n.get("type", "?")] = by_type.get(n.get("type", "?"), 0) + 1
        snap["blue_kg"] = {
            "node_counts": by_type,
            "nodes": [
                {"id": v.get("id"), "type": v.get("type"), "data": v.get("data", {})}
                for v in list(nodes.values())[:200]
            ],
            "edges": list(kg.get("edges", []))[:400],
        }
    else:
        snap["blue_kg"] = None

    # red KG (engagement constraints)
    snap["red_kg"] = _read_json(PKG_DIR / "intel" / "knowledge_graph.json", {})

    # reports / sessions / audits summaries
    ws = _workspace()
    snap["reports"] = [
        {"name": str(f.relative_to(ws)), "size": f.stat().st_size, "mtime": f.stat().st_mtime}
        for f in sorted(
            (ws / "reports").rglob("*") if (ws / "reports").exists() else [],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:50]
        if f.is_file()
    ]
    snap["sessions_count"] = len(list((ws / "sessions").glob("*.json"))) if (ws / "sessions").exists() else 0
    audits = []
    if (ws / "audit_trails").exists():
        for f in sorted((ws / "audit_trails").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            t = _read_json(f, {})
            audits.append(
                {
                    "name": f.name,
                    "engagement": t.get("engagement", f.stem),
                    "started": t.get("started", ""),
                    "actions": t.get("total_actions", 0),
                    "success": t.get("successful_actions", 0),
                    "failed": t.get("failed_actions", 0),
                    "findings": len(t.get("findings", [])),
                    "cost_usd": t.get("cost_usd", 0.0),
                }
            )
    snap["audits"] = audits
    return snap


def _lab_port(app_path: Path) -> int | None:
    text = app_path.read_text(errors="ignore")
    m = re.search(r"port\s*=\s*(\d{4,5})", text) or re.search(r"[Pp]ort[:\s]+(\d{4,5})", text[:4000])
    return int(m.group(1)) if m else None


# ── SSE broadcaster ────────────────────────────────────────────────────

_subscribers: list[queue.Queue] = []
_sub_lock = threading.Lock()


def _broadcast_loop(interval: float = 3.0):
    while True:
        try:
            snap = build_snapshot()
        except Exception:
            snap = {"ts": time.time(), "error": "snapshot failed"}
        with _sub_lock:
            dead = []
            for q in _subscribers:
                try:
                    q.put_nowait(snap)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                with contextlib.suppress(ValueError):
                    _subscribers.remove(q)
        time.sleep(interval)


_broadcaster_started = False


def _ensure_broadcaster():
    global _broadcaster_started
    if not _broadcaster_started:
        threading.Thread(target=_broadcast_loop, daemon=True).start()
        _broadcaster_started = True


# ── App factory ────────────────────────────────────────────────────────


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)

    # ── REST API ──────────────────────────────────────────────────────

    @app.get("/api/overview")
    def overview():
        return jsonify(build_snapshot())

    @app.get("/api/kb/search")
    def kb_search():
        from medusa.tools.intel import search_kb

        q = request.args.get("q", "").strip()
        limit = int(request.args.get("limit", 5))
        if not q:
            return jsonify({"error": "q required"}), 400
        return jsonify({"q": q, "result": search_kb(q, limit=limit)})

    @app.get("/api/report")
    def report_content():
        rel = request.args.get("path", "")
        p = _safe_workspace_file(rel)
        if not p:
            return jsonify({"error": "not found"}), 404
        text = p.read_text(errors="ignore")
        return jsonify({"path": rel, "size": len(text), "content": text[:400_000]})

    @app.get("/api/session")
    def session_detail():
        rel = request.args.get("file", "")
        p = _safe_workspace_file(f"sessions/{rel}") if not rel.startswith("sessions/") else _safe_workspace_file(rel)
        if not p:
            return jsonify({"error": "not found"}), 404
        return jsonify(_read_json(p, {"error": "corrupt"}))

    @app.get("/api/config")
    def config_show():
        from medusa.core.red.config_loader import load_config

        return jsonify(_redact(load_config()))

    @app.get("/api/events")
    def events():
        _ensure_broadcaster()
        q: queue.Queue = queue.Queue(maxsize=4)

        def gen():
            with _sub_lock:
                _subscribers.append(q)
            try:
                # leading snapshot so first paint has data
                with contextlib.suppress(queue.Full):
                    q.put_nowait(build_snapshot())
                while True:
                    try:
                        snap = q.get(timeout=25)
                        yield f"data: {json.dumps(snap)}\n\n"
                    except queue.Empty:
                        yield ": keepalive\n\n"
            finally:
                with _sub_lock, contextlib.suppress(ValueError):
                    _subscribers.remove(q)

        return Response(
            gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    # ── static frontend ───────────────────────────────────────────────

    @app.get("/")
    def index():
        if (DIST_DIR / "index.html").exists():
            return send_from_directory(DIST_DIR, "index.html")
        return (
            "<h1>Medusa UI</h1><p>Frontend not built. Run: <code>cd webui && npm install && npm run build</code></p>"
        ), 200

    @app.get("/<path:path>")
    def static_files(path: str):
        full = (DIST_DIR / path).resolve()
        try:
            full.relative_to(DIST_DIR.resolve())
        except ValueError:
            return jsonify({"error": "forbidden"}), 403
        if full.is_file():
            return send_from_directory(DIST_DIR, path)
        # SPA fallback for client-side routes (/reports, /blue, ...) — but a
        # missing asset (/assets/nope.js) is a real 404, not a route.
        if not path.startswith("assets/") and (DIST_DIR / "index.html").exists():
            return send_from_directory(DIST_DIR, "index.html")
        return jsonify({"error": "not found"}), 404

    return app


def run_server(port: int = 7800, open_browser: bool = True) -> int:
    app = create_app()
    url = f"http://127.0.0.1:{port}"
    print(f"Medusa UI v{VERSION} — {url}  (Ctrl+C to stop)")
    print("  read-only operator console; bound to loopback only")
    if open_browser:
        threading.Timer(0.6, lambda: _open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
    return 0


def _open(url: str):
    import platform
    import subprocess

    sysname = platform.system()
    try:
        if sysname == "Darwin":
            subprocess.Popen(["open", url])
        elif sysname == "Windows":
            subprocess.Popen(["cmd", "/c", "start", url])
        else:
            subprocess.Popen(["xdg-open", url])
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(run_server(open_browser=False))
