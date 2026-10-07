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
