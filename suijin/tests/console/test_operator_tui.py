"""The operator console (operator_tui.py) — the command-center surface.

Six classes, every one pinning a bug class that actually bit in live
runs: digest-shape parity (string findings crashed the render loop),
roster semantics (zombies, audit mixing), chat incremental reads,
render robustness at odd sizes, key/command routing, and aggregation
honesty (dedup, confirmed-only, phase relabel).
"""

from __future__ import annotations

import io
import json
import subprocess
import time
from pathlib import Path

import pytest

from suijin.modules.console.lib import operator_tui as ot


@pytest.fixture()
def demo(tmp_path):
    mesh, ws = tmp_path / "mesh", tmp_path / "ws"
    ot.write_demo(mesh, ws)
    ui = ot.OperatorTUI(mesh, ws, live=False)
    ui.agents = ot.scan_agents(mesh, ws, live=False)
    ui._boot_t0 = 0.0
    return ui, mesh, ws


# ── 1. shape parity — live digests are messier than the demo ────────


def test_scan_accepts_string_findings_and_none_summary(tmp_path):
    """The live crash class: digests publish findings as TITLES (strings)
    and summaries can be None. scan + every consumer must cope."""
    mesh = tmp_path / "mesh"
    mesh.mkdir()
    now = time.time()
    (mesh / "1001.json").write_text(
        json.dumps({"pid": 1001, "summary": None, "phase": "recon", "started": now, "beat": now})
    )
    (mesh / "1001-state.json").write_text(
        json.dumps({"pid": 1001, "phase": "recon", "iteration": 3, "findings": ["a title", "another"]})
    )
    agents = ot.scan_agents(mesh, tmp_path, live=False)
    assert len(agents) == 1
    assert agents[0].target == "?"
    # findings are CONFIRMED exploits only — digest titles count for
    # nothing (but must not crash anything either)
    assert agents[0].findings == []
    assert ot._sev_counts(["a title", "another"])["info"] == 2


def test_scan_accepts_legacy_digests_without_new_keys(tmp_path):
    """Old engagements predate system_one/engagement_dir — scan survives."""
    mesh = tmp_path / "mesh"
    mesh.mkdir()
    now = time.time()
    (mesh / "1002.json").write_text(
        json.dumps({"pid": 1002, "summary": "x", "phase": "recon", "started": now, "beat": now})
    )
    (mesh / "1002-state.json").write_text(json.dumps({"pid": 1002, "phase": "recon", "iteration": 1}))
    agents = ot.scan_agents(mesh, tmp_path, live=False)
    assert agents[0].system_one is None
    assert agents[0].iterations in (None, [])


# ── 2. roster semantics ─────────────────────────────────────────────


def test_dead_pid_dropped_and_pruned(tmp_path):
    mesh = tmp_path / "mesh"
    mesh.mkdir()
    now = time.time()
    dead = {"pid": 999_999, "summary": "x", "phase": "recon", "started": now, "beat": now}
    (mesh / "999999.json").write_text(json.dumps(dead))
    (mesh / "999999-state.json").write_text(json.dumps({"pid": 999_999, "phase": "recon", "iteration": 5}))
    assert ot.scan_agents(mesh, tmp_path, live=True) == []
    assert not (mesh / "999999.json").exists()


def test_zombie_past_24h_dropped_despite_live_pid(tmp_path):
    child = subprocess.Popen(["sleep", "45"])
    try:
        mesh = tmp_path / "mesh"
        mesh.mkdir()
        (mesh / f"{child.pid}.json").write_text(
            json.dumps(
                {
                    "pid": child.pid,
                    "summary": "x",
                    "phase": "recon",
                    "started": time.time() - 90_000,
                    "beat": time.time(),
                }
            )
        )
        assert ot.scan_agents(mesh, tmp_path, live=True) == []
    finally:
        child.kill()
        child.wait()


def test_findings_are_confirmed_exploits_only(demo):
    ui, mesh, ws = demo
    # demo ships 4 CONFIRMED entries + 1 FAILED_TO_CONFIRM (EXP-005)
    total = sum(len(a.findings) for a in ui.agents)
    assert total == 4
    assert all(f["endpoint"].startswith("EXP-") for a in ui.agents for f in a.findings)
    # audit claims never backfill (every demo agent HAS audit findings)
    for a in ui.agents:
        raw_audit = (a.audit_data or {}).get("findings") or []
        assert len(a.findings) <= len(raw_audit)


def test_audit_matched_by_engagement_dir(tmp_path):
    mesh = tmp_path / "mesh"
    eng_root = tmp_path / "engagements"
    mesh.mkdir()
    eng_root.mkdir()
    child = subprocess.Popen(["sleep", "45"])
    try:
        now = time.time()
        iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now))
        for i, cost in ((0, 0.01), (1, 0.99)):
            eng = eng_root / f"20261007_120000_{i}_t"
            (eng / "audit_trails").mkdir(parents=True)
            (eng / "audit_trails" / "t.json").write_text(
                json.dumps({"started": iso, "cost_usd": cost, "findings": [], "iterations": []})
            )
            (mesh / f"{child.pid + i}.json").write_text(
                json.dumps({"pid": child.pid + i, "summary": "t", "phase": "r", "started": now, "beat": now})
            )
            (mesh / f"{child.pid + i}-state.json").write_text(
                json.dumps({"pid": child.pid + i, "engagement_dir": str(eng), "phase": "r", "iteration": 1})
            )
        agents = ot.scan_agents(mesh, tmp_path, live=False)
        by_pid = {a.pid: a for a in agents}
        assert by_pid[child.pid].cost_usd == 0.01
        assert by_pid[child.pid + 1].cost_usd == 0.99
    finally:
        child.kill()
        child.wait()


# ── 3. chat ─────────────────────────────────────────────────────────


def test_read_chat_parses_and_increments(tmp_path):
    mesh = tmp_path / "mesh"
    mesh.mkdir()
    gc = mesh / "gc.log"
    gc.write_text("10-06 12:00:00|111|agent|first\n")
    lines, off = ot.read_chat(mesh)
    assert lines and "first" in lines[0]
    gc.write_text("10-06 12:00:00|111|agent|first\n10-06 12:01:00|222|operator|second\n")
    lines2, off2 = ot.read_chat(mesh, skip_bytes=off)
    assert len(lines2) == 1 and "second" in lines2[0]
    assert off2 > off


def test_chat_line_hex_tag():
    ui = ot.OperatorTUI(Path("/tmp/x"), Path("/tmp/y"))
    t = ui._chat_line("10-06 12:00:00 [agent:37771] lane claim")
    assert "[agent:00938b]" in t.plain


# ── 4. render robustness at odd sizes ───────────────────────────────


@pytest.mark.parametrize("width,height", [(40, 24), (80, 25), (185, 50), (120, 40)])
def test_full_render_at_sizes(demo, width, height, tmp_path):
    ui, mesh, ws = demo
    from rich.console import Console

    shot = Console(width=width, height=height, force_terminal=True, file=io.StringIO())
    layout = ui._build()
    ui._render(layout)
    shot.print(layout)  # must not raise


def test_panels_render_with_boot_stream(demo):
    ui, _, _ = demo
    ui._boot_t0 = time.time()  # stream window active
    assert ui._detail_panel() is not None
    ui._boot_t0 = 0.0


def test_exploits_view_renders(demo):
    ui, _, _ = demo
    ui.exploits_view = True
    body = ui._exploits_panel().renderable.plain
    assert "confirmed exploits" in body
    assert "4 confirmed" in body


# ── 5. keys & commands ──────────────────────────────────────────────


def test_tab_cycles_the_focus_ring(demo):
    ui, _, _ = demo
    seen = [ot.OperatorTUI.AREAS[ui.focus]]
    for _ in range(len(ot.OperatorTUI.AREAS)):
        ui._key("\t")
        seen.append(ot.OperatorTUI.AREAS[ui.focus])
    assert seen[0] == seen[-1]  # wraps
    assert len(set(seen[:-1])) == len(ot.OperatorTUI.AREAS)


def test_jk_page_cards_window(demo):
    ui, _, _ = demo
    ui.focus = ui.AREAS.index("cards")
    ui.scroll = 0
    ui._key("k")  # right: window jumps 5
    assert ui.scroll == 5
    ui._key("k")  # clamped at max
    assert ui.scroll == 5
    ui._key("j")
    assert ui.scroll == 0


def test_detail_arrows_page_history(demo):
    ui, _, _ = demo
    ui.focus = ui.AREAS.index("detail")
    assert ui.iter_pos == 0
    ui._key("\x1b[D")  # older
    assert ui.iter_pos == 1
    ui._key("\x1b[C")  # newer
    assert ui.iter_pos == 0


def test_typing_isolated_to_input_focus(demo):
    ui, _, _ = demo
    ui.focus = ui.AREAS.index("cards")
    ui._key("x")
    assert ui.input_buf == ""  # nothing leaks outside input focus
    ui.focus = ui.AREAS.index("input")
    ui._key("x")
    assert ui.input_buf == "x"


def test_history_recall_beats_prediction(demo):
    ui, _, _ = demo
    ui._execute("/sort cost")
    ui._execute("/say hello team")  # starts with /say — the old swallow bug
    ui.focus = ui.AREAS.index("input")
    ui.input_buf = ""
    ui._key("\x1b[A")
    assert ui.input_buf == "/say hello team"
    ui._key("\x1b[A")
    assert ui.input_buf == "/sort cost"


def test_commands_feedback(demo):
    ui, _, _ = demo
    for line, expect in (
        ("/agent", "usage: /agent <hex>"),
        ("/sort bogus", "usage: /sort iter|cost|finds|uptime"),
        ("/filter bogus", "usage: /filter recon|exploitation|post_exploitation|all"),
        ("/focus nowhere", "usage: /focus cards|details|overview|mesh|input"),
        ("/bogus", "unknown: /bogus"),
        ("/agent 016379", "agent 016379"),
    ):
        ui._execute(line)
        assert ui.note == expect, line


def test_su_identity_switch(demo, tmp_path):
    ui, mesh, ws = demo
    ui._execute("/su 016379")
    assert ui.su_pid is not None
    a = ui._su_agent()
    eng = Path(a.engagement_dir)
    ui._execute("/objective pivot now")
    ui._execute("plain guidance line")
    g = (eng / "state" / "live_guidance.md").read_text()
    assert "[OBJECTIVE]" in g and "[OPERATOR]" in g
    ui._execute("/state")
    assert "016379" in ui.note
    ui._execute("/su root")
    assert ui.su_pid is None
    ui._execute("/next")  # root commands back
    assert "unknown" not in ui.note


def test_su_predictions_follow_identity(demo):
    ui, _, _ = demo
    ui.input_buf = "/"
    root_preds = [k for k, _ in ui._filtered_suggestions()]
    ui._execute("/su 016379")
    ui.input_buf = "/"
    agent_preds = [k for k, _ in ui._filtered_suggestions()]
    assert any(k.startswith("/say") for k in root_preds)
    assert not any(k.startswith("/say") for k in agent_preds)
    assert any(k.startswith("/state") for k in agent_preds)
    assert not any(k.startswith("/state") for k in root_preds)


def test_pause_flag_and_guidance(demo):
    ui, mesh, ws = demo
    ui._execute("/pause wrap up")
    assert (mesh / "pause-operator").exists()
    assert ui.note.startswith("paused all agents")
    guided = list((ws / "engagements").glob("*/state/live_guidance.md"))
    assert guided and all("wrap up" in g.read_text() for g in guided)
    ui._execute("/resume")
    assert not (mesh / "pause-operator").exists()


def test_sort_and_filter_repoll(demo):
    ui, _, _ = demo
    ui._execute("/sort cost")
    assert ui.agents[0].cost_usd >= ui.agents[-1].cost_usd
    ui._execute("/filter exploitation")
    assert ui.agents and all(ot._phase_label(a.phase) == "exploitation" for a in ui.agents)
    ui._execute("/filter all")
    assert len(ui.agents) == 10


def test_clear_suppresses_old_chat(demo):
    ui, mesh, ws = demo
    ui._poll_once()  # chat arrives via the poll, not the fixture
    assert ui.chat
    ui._execute("/clear")
    assert ui.chat == []
    chat, _ = ot.read_chat(mesh, skip_bytes=ui._chat_offset)
    assert chat == []


# ── 6. aggregation honesty ──────────────────────────────────────────


def test_informational_relabels_recon(demo):
    assert ot._phase_label("informational") == "recon"
    assert ot._phase_label("exploitation") == "exploitation"


def test_header_and_overview_dedup_and_totals(demo):
    ui, _, _ = demo
    finds = ui._all_dict_findings()
    keys = [(f.get("type"), f.get("endpoint")) for f in finds]
    assert len(keys) == len(set(keys))  # deduped
    sc = ot._sev_counts(finds)
    assert sum(sc.values()) == len(finds)  # totals = severity sum, no phantoms


def test_system_one_aggregates(demo):
    ui, _, _ = demo
    body = ui._system_one_text().plain
    assert "laya-mlx" in body and "loaded" in body
    ui.agents = []  # nothing running
    assert "not running" in ui._system_one_text().plain


def test_header_strip_carries_dollar(demo):
    ui, _, _ = demo
    from rich.console import Console

    c = Console(width=200, force_terminal=True, record=True, file=io.StringIO())
    c.print(ui._header())
    assert "$" in c.export_text()
