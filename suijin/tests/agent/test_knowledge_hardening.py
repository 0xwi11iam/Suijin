"""The 2026-10-07 knowledge-hardening round: lab/real isolation and
machine-driven comms. Every guard here exists because live engagements
hit the bug it pins."""

from __future__ import annotations

import json

import pytest

from suijin.modules.agent.lib.attack_memory import is_lab_target

# ── lab target classification (the contamination fence) ──────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("127.0.0.1:6000", True),
        ("http://127.0.0.1:8443/x", True),
        ("localhost:5000", True),
        ("lab.local", True),
        ("example.com", False),
        ("http://example.com:443", False),
        ("", False),
    ],
)
def test_is_lab_target(text, expected):
    assert is_lab_target(text) is expected


def test_what_worked_class_transfer_excludes_lab(tmp_path, monkeypatch):
    """Real catalogs feed class-transfer advice; lab catalogs never do —
    a lab decoy flag must not whisper into a real engagement."""
    import suijin.modules.agent.lib.attack_memory as am
    import suijin.modules.platform.lib.workspace as ws

    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    for name, target, cls in (
        ("20260101_000000_real.example_a", "real.example", "ssrf"),
        ("20260101_000001_127_0_0_1_6000", "127.0.0.1:6000", "ssti"),
    ):
        d = tmp_path / "engagements" / name / "exploits" / "T"
        d.mkdir(parents=True)
        (d / "catalog.json").write_text(
            json.dumps(
                {
                    "entries": {
                        "EXP-001": {
                            "id": "EXP-001",
                            "status": "CONFIRMED",
                            "class": cls,
                            "target": target,
                            "title": "t",
                        }
                    }
                }
            )
        )
    lines = am.what_worked("other.example")
    joined = " ".join(lines)
    assert "ssrf×1" in joined  # real target counted
    assert "ssti" not in joined  # lab target excluded


def test_record_finding_rejects_lab_markers_on_real_targets(tmp_path, monkeypatch):
    """FLAG{...} on a real target is contamination, not a finding."""

    monkeypatch.setenv("SUIJIN_KG_BACKEND", "json")
    from suijin.modules.redteam.lib.intel import knowledge_graph as kg
    from suijin.modules.redteam.lib.intel.kg_backend import _invalidate_backend_cache

    _invalidate_backend_cache()
    monkeypatch.setattr(kg, "GRAPH_PATH", tmp_path / "kg.json")

    from suijin.modules.tools.lib.intel import record_finding

    out = record_finding("example.com", "behavior", "FLAG{northbridge_decoy_admin_bypass}", "saw it")
    assert out.startswith("REJECTED")
    assert "lab/CTF artifacts" in out

    lab = record_finding("127.0.0.1:6000", "behavior", "FLAG{ok_in_lab}", "lab run")
    assert not str(lab).startswith("REJECTED")


def test_cheatsheet_add_stamps_origin(tmp_path, monkeypatch):
    from suijin.modules.agent.lib import cheatsheet as cs
    from suijin.modules.platform.lib import workspace as ws

    monkeypatch.setattr(ws, "_CURRENT_ENGAGEMENT", tmp_path / "eng_20261007_example")
    cs._cache.update(mtime=0.0, data=None)
    monkeypatch.setattr(cs, "_load", lambda: [])
    monkeypatch.setattr(cs, "_save", lambda rows: None)
    monkeypatch.setattr(
        cs,
        "engagement_dir",
        lambda: tmp_path / "eng_20261007_example",
        raising=False,
    )
    # origin comes from the live workspace seam — patch at the source
    import suijin.modules.agent.lib.cheatsheet as csm

    csm._cache.update(mtime=0.0, data=None)
    rows = []
    monkeypatch.setattr(csm, "_load", lambda: rows)
    monkeypatch.setattr(csm, "_save", lambda r: rows.extend(r[-1:]) or None)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "suijin.modules.platform.lib.workspace.engagement_dir",
            lambda: tmp_path / "eng_20261007_example",
        )
        out = csm.add("test snippet", tag="x")
    assert "stored" in out
    assert rows[-1]["origin"] == "eng_20261007_example"


def test_set_phase_broadcasts_system_line(tmp_path, monkeypatch):
    """A phase transition lands a [system] line in gc.log — machine-driven,
    every peer sees it without model choice."""
    import os

    import suijin.modules.agent.lib.mesh as mesh

    monkeypatch.setattr(mesh, "mesh_dir", lambda: tmp_path)
    mesh._node["me"] = {"pid": os.getpid(), "summary": "t", "phase": "recon", "started": 0.0}
    try:
        mesh.set_phase("exploitation")  # transition -> broadcast
        mesh.set_phase("exploitation")  # same phase -> silent
    finally:
        mesh._node["me"] = None
    lines = (tmp_path / "gc.log").read_text().splitlines()
    sys_lines = [ln for ln in lines if "|system|" in ln]
    assert len(sys_lines) == 1
    assert "phase → exploitation" in sys_lines[0]


def test_on_confirmed_writes_kg(tmp_path, monkeypatch):
    """A CONFIRMED exploit lands in the knowledge graph automatically."""
    monkeypatch.setenv("SUIJIN_KG_BACKEND", "json")
    from suijin.modules.redteam.lib.intel import knowledge_graph as kg
    from suijin.modules.redteam.lib.intel.kg_backend import _invalidate_backend_cache

    _invalidate_backend_cache()
    monkeypatch.setattr(kg, "GRAPH_PATH", tmp_path / "kg.json")

    from suijin.modules.tools.lib.exploit_catalog import _on_confirmed

    _on_confirmed(
        {
            "id": "EXP-001",
            "class": "ssti",
            "severity": "critical",
            "target": "example.com",
            "title": "template injection",
        }
    )
    cons = kg.get_constraints("example.com")
    assert cons.get("verified") and "ssti: template injection" in cons["verified"][0]["rule"]


def test_audit_cost_updates_live_per_iteration(tmp_path, monkeypatch):
    """Window-close mid-engagement must still show what it spent: the
    trail cost refreshes EVERY iteration from the provider accumulator
    (models.dev-priced), not only at end_audit — unclean exits died at $0.000."""

    monkeypatch.setenv("SUIJIN_ENV", str(tmp_path / ".env"))
    import suijin.modules.tools.lib.audit_trail as at

    at.start_audit("costtest.example") if hasattr(at, "start_audit") else None
    import suijin.modules.providers.lib as providers

    monkeypatch.setitem(providers.USAGE, "est_cost_usd", 0.1234)
    at.log_iteration(1, "t", "r", "http_request", {}, "ok", True, "recon")
    trail = at._trails[at._engagement_key()]
    assert trail["cost_usd"] == 0.1234
    monkeypatch.setitem(providers.USAGE, "est_cost_usd", 0.25)
    at.log_iteration(2, "t", "r", "http_request", {}, "ok", True, "recon")
    assert trail["cost_usd"] == 0.25


class TestCrossTargetCredentialGuard:
    """One credential is not simultaneously three targets'
    (shared-/tmp reads fooled three concurrent engagements into
    claiming the same key, 2026-10-08)."""

    KEY = "AIzaSy" + "Example0Example0Example0Example0Xy"

    def _kg(self, tmp_path, monkeypatch):

        monkeypatch.setenv("SUIJIN_KG_BACKEND", "json")
        from suijin.modules.redteam.lib.intel import knowledge_graph as kg
        from suijin.modules.redteam.lib.intel.kg_backend import _invalidate_backend_cache

        _invalidate_backend_cache()
        monkeypatch.setattr(kg, "GRAPH_PATH", tmp_path / "kg.json")
        return kg

    def test_credential_already_on_other_target_rejected(self, tmp_path, monkeypatch):
        kg = self._kg(tmp_path, monkeypatch)
        kg.add_constraint("maps.example", "verified", f"key {self.KEY} leaked", confidence=1.0)
        from suijin.modules.tools.lib.intel import record_finding

        out = record_finding("shop.example", "behavior", f"Google key {self.KEY}", "saw it")
        assert out.startswith("REJECTED")
        assert "maps.example" in out
        assert not kg.get_constraints("shop.example")

    def test_same_host_www_variant_not_rejected(self, tmp_path, monkeypatch):
        kg = self._kg(tmp_path, monkeypatch)
        kg.add_constraint("example.com", "verified", f"key {self.KEY}", confidence=1.0)
        from suijin.modules.tools.lib.intel import record_finding

        out = record_finding("www.example.com", "behavior", f"key {self.KEY}", "same site")
        assert not str(out).startswith("REJECTED")

    def test_fresh_credential_records_normally(self, tmp_path, monkeypatch):
        self._kg(tmp_path, monkeypatch)
        from suijin.modules.tools.lib.intel import record_finding

        out = record_finding("example.com", "behavior", f"sk-{'a' * 30}", "fresh")
        assert not str(out).startswith("REJECTED")


def test_terminal_warns_on_literal_tmp():
    """Hardcoded /tmp escapes TMPDIR confinement — the observation carries
    the warning the agent reads next turn."""
    from suijin.modules.tools.lib.terminal import execute_terminal

    out = execute_terminal("ls /tmp", timeout=10)
    assert "SHARED by all concurrent engagements" in out
    out2 = execute_terminal("echo hi", timeout=10)
    assert "SHARED" not in out2


class TestPostmortemFixes:
    """Field-run lessons (postmortem 2026-10-08), each pinned."""

    def test_oracle_mismatch_detector(self):
        from suijin.modules.agent.lib.supervisor import _detect_question_recycling

        spiral = [{"thought": f"Decisive script #{i} to settle the /shop/goto chain"} for i in range(3)]
        out = _detect_question_recycling(spiral)
        assert out and "ORACLE MISMATCH" in out and "mcp_browser_goto" in out
        assert _detect_question_recycling([{"thought": "map the surface"}] * 5) is None

    def test_write_file_returns_absolute_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SUIJIN_ENV", str(tmp_path / ".env"))
        from suijin.modules.tools.lib.http_tools import write_file

        r = write_file("sweep.py", "print(1)")
        assert r.startswith("File written: /")
        assert "Workspace label:" in r

    def test_job_output_names_running_state(self):
        import time

        from suijin.modules.tools.lib import job_registry as jr

        jid = jr.spawn("execute_terminal", {}, lambda *a, **k: time.sleep(30), label="sleeper")
        try:
            out = jr.output(jid)
            assert "STILL RUNNING" in out and "taps" in out
        finally:
            jr.cancel(jid)

    def test_librarian_hides_cross_target_entries(self, tmp_path):
        from suijin.modules.agent.lib.librarian import Librarian

        lib = Librarian(None, tmp_path, interval=10, target="shop.example")
        lib._ledger["entries"] = [
            {"kind": "cred", "value": "lab-cred", "where": "w", "iter": 1, "run": "r1", "target": "127.0.0.1:6000"},
            {"kind": "cred", "value": "site-key", "where": "w", "iter": 2, "run": "r1", "target": "shop.example"},
            {"kind": "note", "value": "legacy", "where": "w", "iter": 3, "run": "r1"},
        ]
        out = lib.render_ledger()
        assert "lab-cred" not in out
        assert "site-key" in out and "legacy" in out
        assert "cross-target observations hidden" in out
