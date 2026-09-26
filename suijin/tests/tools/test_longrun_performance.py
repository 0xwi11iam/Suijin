"""Long-run performance regressions from a live engagement.

Symptoms in the field: as an engagement runs longer and observations
stack up, the whole console becomes extremely laggy and typing stops
registering; and every verify=False HTTP request printed an
InsecureRequestWarning into the log the operator was following.

Root causes (both structural, both fixed here):

1. The audit trail was rewritten IN FULL on every log call. Observations
   are stored untruncated by design, so the file grows linearly with the
   run — making the write cost per tool call grow linearly too, as one
   GIL-held json.dumps of the entire history. The UI's threads starve.
   Writes are now interval-guarded with a forced flush at end-of-run.

2. The detached daemon child never ran init_runtime, so urllib3's warning
   suppression (a boot-time side effect) was missing on that path.

Every host here is example.com — engagement targets are never named in
committed artifacts (see AGENTS.md).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def trail(tmp_path, monkeypatch):
    import suijin.modules.tools.lib.audit_trail as at

    monkeypatch.setattr(at, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(at, "_last_flush", 0.0)
    monkeypatch.setattr(at, "_dirty", False)
    at.start_audit("example-engagement")
    yield at, tmp_path
    at._current_trail = None


class TestAuditTrailWriteCadence:
    def test_bursts_are_throttled(self, trail, monkeypatch):
        """Many log calls in quick succession produce AT MOST one disk
        write after the first — not one full rewrite per call."""
        at, tmp_path = trail
        writes = []
        real_write = Path.write_text

        def counting_write(self, *a, **kw):
            writes.append(self.name)
            return real_write(self, *a, **kw)

        monkeypatch.setattr(Path, "write_text", counting_write)
        for i in range(25):
            at.log_iteration(i, "t", "r", "http_request", {"url": "https://example.com"}, "x" * 2000, True, "recon")
        # start_audit's forced initial write + at most one throttled write
        assert len([w for w in writes if w.endswith(".json")]) <= 2, writes

    def test_everything_lands_at_the_forced_end(self, trail):
        """The throttle never loses data: end_audit forces the full trail."""
        at, tmp_path = trail
        for i in range(30):
            at.log_iteration(i, "t", "r", "http_request", {}, "obs", True, "recon")
        at.log_finding("xss", "high", "https://example.com/search", "reflected", "proof")
        path = at.end_audit(0.5)
        data = json.loads((tmp_path / "example-engagement.json").read_text())
        assert len(data["iterations"]) == 30
        assert data["findings"][0]["endpoint"] == "https://example.com/search"
        assert data["cost_usd"] == 0.5
        assert path.endswith(".md")

    def test_flush_is_cheap_when_clean(self, trail):
        at, _ = trail
        at.flush()
        at.flush()
        at.flush()  # no exception, no forced rewrite storm

    def test_serialization_is_compact(self, trail):
        """indent=2 was pure formatting cost on a file only tools read."""
        at, tmp_path = trail
        at.log_iteration(1, "t", "r", "http_request", {}, "obs", True, "recon")
        at.end_audit(0.0)
        raw = (tmp_path / "example-engagement.json").read_text()
        assert "\n" not in raw  # one compact line, not pretty-printed
        assert json.loads(raw)["iterations"]  # and still valid JSON


class TestDaemonChildBoot:
    def test_run_daemon_child_initializes_the_runtime(self, monkeypatch):
        """The detached child went straight into the engagement, skipping
        every init_runtime boot semantic — most visibly urllib3 warning
        suppression, which flooded the run log the operator follows."""
        from suijin.modules.ops.lib import daemon as dmod

        called = []
        monkeypatch.setattr("suijin.modules.platform.lib.runtime.init_runtime", lambda *a, **k: called.append(1))
        monkeypatch.setattr(dmod, "_read_record", lambda rid: {})
        assert dmod.run_daemon_child("nope") == 2  # unknown record: exits clean
        assert called, "init_runtime was never called on the daemon path"


class TestWarningSuppressionAtTheHttpLayer:
    def test_importing_http_tools_silences_insecure_request_warnings(self):
        """Boot suppresses these via init_runtime; the tool layer must too,
        so any import path (verbs, tests, tools) stays quiet — the warning
        goes to stderr and tears any live display that owns the console."""
        import subprocess
        import sys

        code = (
            "import warnings, urllib3.exceptions as ex;"
            "from suijin.modules.tools.lib import http_tools;"  # noqa: F841 — import is the act
            "print(any(f[0]=='ignore' and f[2] is ex.InsecureRequestWarning for f in warnings.filters))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parents[3]),
            timeout=120,
        )
        assert out.returncode == 0, out.stderr[-400:]
        assert out.stdout.strip().endswith("True"), (
            "no ignore-filter for InsecureRequestWarning after importing the HTTP tools"
        )


class TestMemoryNote:
    def test_note_is_deduped_and_compact(self, tmp_path, monkeypatch):
        from suijin.modules.agent.lib import memory as mem

        monkeypatch.setattr(mem, "_mem_dir", lambda: tmp_path)
        mem.note("example.com", "operator confirmed scope")
        mem.note("example.com", "operator confirmed scope")  # identical tail
        f = tmp_path / "example.com.json"
        data = json.loads(f.read_text())
        assert data["operator_notes"] == ["operator confirmed scope"]
        assert "\n" not in f.read_text()  # compact, not indent=2

    def test_distinct_notes_all_kept(self, tmp_path, monkeypatch):
        from suijin.modules.agent.lib import memory as mem

        monkeypatch.setattr(mem, "_mem_dir", lambda: tmp_path)
        mem.note("example.com", "first")
        mem.note("example.com", "second")
        data = json.loads((tmp_path / "example.com.json").read_text())
        assert data["operator_notes"] == ["first", "second"]
