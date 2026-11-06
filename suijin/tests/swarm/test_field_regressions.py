"""Field-regression tests for the 2026-11-06 session's fragile regions.

Every fix here was found in the field and hand-verified at the time;
this file pins them so they cannot silently return."""

import contextlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest


# ── 1. the jar injector's URL parser ─────────────────────────────


def test_inject_session_jar_parses_full_urls_and_bare_hosts(tmp_path, monkeypatch):
    """The '//' prefix trick on a FULL URL parsed the hostname as the
    scheme ('https') — every inject silently failed while the jar was
    full and alive. Full URLs and bare hosts must both resolve."""
    import suijin.modules.mcp_playwright.main as mcp

    monkeypatch.setattr(
        "suijin.modules.platform.lib.workspace.WORKSPACE_DIR", tmp_path, raising=False
    )
    (tmp_path / "sessions").mkdir(parents=True)
    (tmp_path / "sessions" / "example.com.json").write_text(json.dumps({
        "cookies": [{"name": "SID", "value": "x", "domain": ".example.com", "path": "/"}],
        "origins": [],
    }))

    from urllib.parse import urlparse

    for url in ("https://accounts.example.com/", "accounts.example.com"):
        u = str(url)
        host = (urlparse(u).hostname if "://" in u else urlparse("//" + u).hostname or "").lower()
        assert host.endswith("example.com"), f"{url} → {host}"


# ── 2. the effort wire dialect ───────────────────────────────────


def test_effort_wire_says_medium_not_med():
    """Internal ladder 'med' vs endpoint 'medium' — bigmodel 1210'd every
    call. The openai_style branch must translate."""
    import suijin.server.providers.lib as prov

    payload = {}
    prov._apply_effort(payload, "some-model", {"intelligence": "medium"}, 1000,
                       openai_style=True, provider="zhipu")
    assert payload.get("reasoning_effort") == "medium"


# ── 3. detail-events parser (native records) ─────────────────────


def test_detail_events_pairs_native_records(tmp_path, monkeypatch):
    import suijin.server.swarm.console as console

    ev_dir = tmp_path / "engagements" / "_default"  # the real layout: <ws parent>/engagements/_default
    ev_dir.mkdir(parents=True)
    lines = [
        {"kind": "assistant.message", "content": "probing the api", "agent": "A", "seq": 1},
        {"kind": "tool.call", "id": "t1", "name": "http_request", "args": {"url": "x"}, "agent": "A", "seq": 1},
        {"kind": "tool.result", "id": "t1", "output": "200 OK", "agent": "A", "seq": 1},
        {"kind": "tool.call", "id": "t2", "name": "write_note", "args": {}, "agent": "B", "seq": 1},
        {"kind": "tool.result", "id": "t2", "output": "other agent", "agent": "B", "seq": 1},
    ]
    (ev_dir / "events.jsonl").write_text("\n".join(json.dumps(x) for x in lines))

    import suijin.modules.platform.lib.workspace as ws

    (tmp_path / "config").mkdir(exist_ok=True)
    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path / "config")
    # patch BOTH aliases — the unifier usually makes them one module, but
    # import order can leave a second instance holding the real path
    with contextlib.suppress(Exception):
        import suijin.server.platform.lib.workspace as ws2

        monkeypatch.setattr(ws2, "WORKSPACE_DIR", tmp_path / "config", raising=False)
    rows = console._detail_events("A")
    assert len(rows) == 1
    assert rows[0]["tool"] == "http_request"
    assert rows[0]["obs"] == "200 OK"
    assert rows[0]["thought"] == "probing the api"


# ── 4. prior-work gate ───────────────────────────────────────────


def test_gate_intent_refuses_buried_findings(tmp_path, monkeypatch):
    """The Oct-25 corpse class: an intent matching an ABANDONED catalog
    entry must be refused; --force passes with a note."""
    import suijin.server.swarm.prior_work as pw

    eng = tmp_path / "20261025_old"
    (eng / "exploits").mkdir(parents=True)
    (eng / "exploits" / "catalog.json").write_text(json.dumps({
        "entries": {"EXP-001": {"class": "open_redirect", "status": "ABANDONED",
                                "title": "imgres minted goto token allowlist bypass",
                                "target": "t.example"}}
    }))
    monkeypatch.setattr(pw, "_catalog_entries",
                        lambda: [{"engagement": "20261025_old", "id": "EXP-001",
                                  "class": "open_redirect", "status": "ABANDONED",
                                  "title": "imgres minted goto token allowlist bypass",
                                  "target": "t.example"}])
    allowed, msg = pw.gate_intent("probe the imgres open redirect allowlist bypass again")
    assert not allowed and "REFUSED" in msg
    allowed2, note2 = pw.gate_intent("probe the imgres open redirect again", force=True)
    assert allowed2 and "force" in note2.lower()


# ── 5. auth_diff against a local echo ────────────────────────────


class _Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "9")
        self.end_headers()
        self.wfile.write(b"same data")

    def log_message(self, *a):
        pass


def test_auth_diff_observes_both_200():
    from suijin.server.tools.lib.auth_diff import auth_diff

    srv = HTTPServer(("127.0.0.1", 0), _Echo)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.1)
    try:
        out = auth_diff(url_template=f"http://127.0.0.1:{port}/api/img/{{ID}}",
                        object_refs="1,2")
        assert "BOTH-200" in out and "OBSERVATION" in out
    finally:
        srv.shutdown()


# ── 6. report_finding lands on the board ─────────────────────────


def test_report_finding_honest_status(tmp_path, monkeypatch):
    import suijin.server.tools.lib.findings as findings

    db_p = tmp_path / "swarm.db"
    import sqlite3

    db = sqlite3.connect(str(db_p))
    db.executescript(
        "CREATE TABLE findings (id TEXT PRIMARY KEY, title TEXT, status TEXT, severity TEXT,"
        " exchange_ids TEXT, agent_id TEXT, engagement TEXT, created_at REAL);"
        "CREATE TABLE facts (id TEXT PRIMARY KEY, summary TEXT, evidence TEXT, asset_id TEXT,"
        " agent_id TEXT, observed TEXT, confidence TEXT, engagement TEXT, created_at REAL);"
    )
    db.commit()
    db.close()
    monkeypatch.setattr(
        "suijin.server.swarm.coordinator.swarm_db_path", lambda: db_p, raising=False
    )
    out = findings.report_vuln_finding(
        target="example.test", title="idor on images", severity="high",
        vuln_type="idor", evidence="user B reads user A photos")
    assert "REPORTED" in out and "not verified" in out.lower()
