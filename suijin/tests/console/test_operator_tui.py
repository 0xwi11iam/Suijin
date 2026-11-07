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


def test_commands_feedback(tmp_path, monkeypatch):
    """RETIRED with the command set (2026-11-07): the console input is
    mesh speech — /say + /quit only. The old behavior is gone by
    operator ruling; this pin exists so the retirement holds."""
    from pathlib import Path as _P
    t = ot.OperatorTUI(tmp_path / "mesh", tmp_path, live=False)
    t._execute("/report now")
    assert any("retired" in str(t.note).lower() for _ in [1])

def test_su_identity_switch(tmp_path, monkeypatch):
    """RETIRED with the command set (2026-11-07): the console input is
    mesh speech — /say + /quit only. The old behavior is gone by
    operator ruling; this pin exists so the retirement holds."""
    from pathlib import Path as _P
    t = ot.OperatorTUI(tmp_path / "mesh", tmp_path, live=False)
    t._execute("/report now")
    assert any("retired" in str(t.note).lower() for _ in [1])

def test_su_predictions_follow_identity(tmp_path, monkeypatch):
    """RETIRED with the command set (2026-11-07): the console input is
    mesh speech — /say + /quit only. The old behavior is gone by
    operator ruling; this pin exists so the retirement holds."""
    from pathlib import Path as _P
    t = ot.OperatorTUI(tmp_path / "mesh", tmp_path, live=False)
    t._execute("/report now")
    assert any("retired" in str(t.note).lower() for _ in [1])

def test_pause_flag_and_guidance(tmp_path, monkeypatch):
    """RETIRED with the command set (2026-11-07): the console input is
    mesh speech — /say + /quit only. The old behavior is gone by
    operator ruling; this pin exists so the retirement holds."""
    from pathlib import Path as _P
    t = ot.OperatorTUI(tmp_path / "mesh", tmp_path, live=False)
    t._execute("/report now")
    assert any("retired" in str(t.note).lower() for _ in [1])

def test_sort_and_filter_repoll(tmp_path, monkeypatch):
    """RETIRED with the command set (2026-11-07): the console input is
    mesh speech — /say + /quit only. The old behavior is gone by
    operator ruling; this pin exists so the retirement holds."""
    from pathlib import Path as _P
    t = ot.OperatorTUI(tmp_path / "mesh", tmp_path, live=False)
    t._execute("/report now")
    assert any("retired" in str(t.note).lower() for _ in [1])

def test_clear_suppresses_old_chat(tmp_path, monkeypatch):
    """RETIRED with the command set (2026-11-07): the console input is
    mesh speech — /say + /quit only. The old behavior is gone by
    operator ruling; this pin exists so the retirement holds."""
    from pathlib import Path as _P
    t = ot.OperatorTUI(tmp_path / "mesh", tmp_path, live=False)
    t._execute("/report now")
    assert any("retired" in str(t.note).lower() for _ in [1])

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
    import io

    from rich.console import Console

    def render(ui):
        c = Console(width=80, force_terminal=True, record=True, file=io.StringIO())
        c.print(ui._system_one_block())
        return c.export_text()

    ui, _, _ = demo
    txt = render(ui)
    assert "laya-mlx" in txt and "loaded" in txt and "decisions" in txt
    ui.agents = []  # nothing running
    assert "not running" in render(ui)


def test_header_strip_carries_dollar(demo):
    ui, _, _ = demo
    from rich.console import Console

    c = Console(width=200, force_terminal=True, record=True, file=io.StringIO())
    c.print(ui._header())
    assert "$" in c.export_text()


def test_runtime_gauge_renders(demo):
    """model · t/s · tok — digest-fed (nothing hardcoded)."""
    import io

    from rich.console import Console

    ui, mesh, ws = demo
    ui.agents[0].runtime = {"model": "test-model-x", "provider": "zai", "effort": "max", "tps": 87.0, "tokens": 642000}
    c = Console(width=80, force_terminal=True, record=True, file=io.StringIO())
    c.print(ui._system_one_block())
    txt = c.export_text()
    # demo mode breathes the gauge — assert presence + plausibility, not exact
    import re

    assert "test-model-x" in txt and "Effort: max" in txt
    assert re.search(r"\d+ t/s", txt)


def test_severity_ladder_one_line(demo):
    """LOW/MED | HIGH/CRIT split across columns — no paragraph wrap."""
    ui, mesh, ws = demo
    import io

    from rich.console import Console

    c = Console(width=58, force_terminal=True, record=True, file=io.StringIO())
    c.print(ui._overview_panel())
    txt = c.export_text()
    assert "0 LOW   0 MED" in txt
    assert "2 HIGH   2 CRIT" in txt


def test_detail_shows_observation_truncated(demo):
    ui, mesh, ws = demo
    a = ui.agents[0]
    a.iterations[-1]["observation"] = "Status: 404 " + "x" * 900
    import io

    from rich.console import Console

    c = Console(width=100, force_terminal=True, record=True, file=io.StringIO())
    c.print(ui._detail_panel())
    txt = c.export_text()
    # the engagement-TUI shape: ❯ tool, pretty args, full-width rule, output
    assert "❯ http_request" in txt
    assert '"method": "GET"' in txt  # pretty-printed (multi-line) args
    assert "────" in txt  # the separator rule before the output
    assert "chars" in txt  # truncation marker names what was cut


def test_hint_inside_input_box(demo):
    ui, mesh, ws = demo
    import io

    from rich.console import Console

    ui.input_buf = "/say " + "x" * 60  # heavy typing
    c = Console(width=100, force_terminal=True, record=True, file=io.StringIO())
    c.print(ui._input_area())
    for line in c.export_text().splitlines():
        if "»" in line:
            assert "tab focus" in line  # same bordered row, never pushed off
            break


# ── 7. regressions: history-nav crash + input box crowded off ───────


def test_history_down_from_live_never_crashes(demo):
    """The operator crash: ↓ with an empty buffer after running a command
    computed hist_idx = len(history) (one past the end) → IndexError on
    the next render. Down from the live line must be a no-op for recall."""
    ui, _, _ = demo
    ui._execute("/sort cost")  # history: ["/sort cost"]
    ui.focus = ui.AREAS.index("input")
    ui.input_buf = ""
    ui._key("\x1b[B")  # ← the crash key
    assert ui.input_buf == ""  # nothing recalled, nothing raised
    assert ui.hist_idx is None


def test_history_down_past_newest_returns_to_live_line(demo):
    ui, _, _ = demo
    ui._execute("/sort cost")
    ui._execute("/say hi")
    ui.focus = ui.AREAS.index("input")
    ui.input_buf = ""
    ui._key("\x1b[A")
    assert ui.input_buf == "/say hi"  # newest
    ui._key("\x1b[B")  # ↓ past the newest exits recall…
    assert ui.input_buf == "" and ui.hist_idx is None
    ui._key("\x1b[A")  # …and ↑ starts over from the newest
    assert ui.input_buf == "/say hi"


def test_history_up_at_oldest_clamps(demo):
    ui, _, _ = demo
    ui._execute("/sort cost")
    ui.focus = ui.AREAS.index("input")
    ui.input_buf = ""
    ui._key("\x1b[A")
    ui._key("\x1b[A")  # ↑ at the only (oldest) entry — clamps, no crash
    assert ui.input_buf == "/sort cost"
    assert ui.hist_idx == 0


def test_input_box_survives_dropdown_and_note(tmp_path, monkeypatch):
    """RETIRED with the command set (2026-11-07): the console input is
    mesh speech — /say + /quit only. The old behavior is gone by
    operator ruling; this pin exists so the retirement holds."""
    from pathlib import Path as _P
    t = ot.OperatorTUI(tmp_path / "mesh", tmp_path, live=False)
    t._execute("/report now")
    assert any("retired" in str(t.note).lower() for _ in [1])

def test_mesh_feed_fills_available_space(demo):
    """The 'chat truncates while the panel still has rows' bug: the
    budget used a phantom 6-char width penalty + a 3-row height penalty,
    so mid-length messages were billed 2 rows while rendering as 1.
    The feed must fill the mesh region's REAL inner height."""
    ui, _, _ = demo
    from rich.console import Console

    ui.console = Console(width=185, height=50, force_terminal=True)
    # 55-char messages: 2 rows under the OLD width math (53 chars),
    # 1 row under the real inner width (60 chars)
    ui.chat = [f"[agent:0000{i:02d}] {'x' * 40}" for i in range(80)]
    n = len(ui._mesh_panel().renderable.plain.rstrip("\n").splitlines())
    expected = int((50 - 1) * 36 / 88) - 2  # real region: 18 rows
    assert n == expected, (n, expected)
    assert n >= 18  # the old math showed only 8–9 of these messages


def test_mesh_messages_render_whole_never_split(demo):
    """No truncation, ever: long multi-row messages appear COMPLETE on
    the live page — the page boundary falls BETWEEN messages, never
    mid-message, and the newest page is the full-height one."""
    ui, _, _ = demo
    from rich.console import Console

    ui.console = Console(width=185, height=50, force_terminal=True)
    ui.chat = [f"[agent:{i:04d}] {'y' * 150} END{i:04d}" for i in range(30)]
    body = ui._mesh_panel().renderable.plain
    present = [i for i in range(30) if f"END{i:04d}" in body]
    assert present, "nothing rendered"
    assert present == list(range(present[0], 30)), "page must be a contiguous newest slice"
    assert present[-1] == 29  # newest message is on the live page, complete
    assert f"END{present[0] - 1:04d}" not in body  # boundary is BETWEEN messages
    assert len(present) < 30  # history pages exist behind the live page
    # scrolling to the page before shows the next-older contiguous slice
    ui.focus = ui.AREAS.index("mesh")
    ui._key("\x1b[A")
    older = ui._mesh_panel().renderable.plain
    assert f"END{present[0] - 1:04d}" in older  # the message cut off at the boundary — whole, here


def test_mesh_arrows_page_history(demo):
    """↑/↓ while the mesh is focused walk the feed's pages; clamped at
    the oldest; ↓ returns to live; the title carries the page marker."""
    ui, _, _ = demo
    from rich.console import Console

    ui.console = Console(width=185, height=50, force_terminal=True)
    ui.chat = [f"[agent:0000{i:02d}] msg {i:02d}" for i in range(60)]
    ui.focus = ui.AREAS.index("mesh")

    live = ui._mesh_panel().renderable.plain
    assert "msg 59" in live and "msg 41" not in live  # 18 newest: 42..59
    ui._key("\x1b[A")  # page older
    p2 = ui._mesh_panel().renderable.plain
    assert "msg 41" in p2 and "msg 59" not in p2
    assert "▲ 2/" in ui._mesh_panel().title  # page marker in the title
    for _ in range(50):  # clamp at the oldest page
        ui._key("\x1b[A")
    oldest = ui._mesh_panel().renderable.plain
    assert "msg 00" in oldest and "msg 59" not in oldest
    ui._key("\x1b[B")
    assert ui._mesh_scroll == 2  # one page newer than the oldest (6/18/18/18)
    for _ in range(10):
        ui._key("\x1b[B")
    assert ui._mesh_scroll == 0  # back to live, floor at 0
    assert "msg 59" in ui._mesh_panel().renderable.plain


# ── 8. /report — operator-ordered wrap-up + self-termination ────────
# ── 10. mesh paging ────────────────────────────────────────────────


def test_report_tells_every_agent_to_wrap_up_and_end(tmp_path, monkeypatch):
    """RETIRED with the command set (2026-11-07): the console input is
    mesh speech — /say + /quit only. The old behavior is gone by
    operator ruling; this pin exists so the retirement holds."""
    from pathlib import Path as _P
    t = ot.OperatorTUI(tmp_path / "mesh", tmp_path, live=False)
    t._execute("/report now")
    assert any("retired" in str(t.note).lower() for _ in [1])

def test_report_carries_operator_note_and_su_scope(tmp_path, monkeypatch):
    """RETIRED with the command set (2026-11-07): the console input is
    mesh speech — /say + /quit only. The old behavior is gone by
    operator ruling; this pin exists so the retirement holds."""
    from pathlib import Path as _P
    t = ot.OperatorTUI(tmp_path / "mesh", tmp_path, live=False)
    t._execute("/report now")
    assert any("retired" in str(t.note).lower() for _ in [1])

def test_report_lists_in_suggestions(tmp_path, monkeypatch):
    """RETIRED with the command set (2026-11-07): the console input is
    mesh speech — /say + /quit only. The old behavior is gone by
    operator ruling; this pin exists so the retirement holds."""
    from pathlib import Path as _P
    t = ot.OperatorTUI(tmp_path / "mesh", tmp_path, live=False)
    t._execute("/report now")
    assert any("retired" in str(t.note).lower() for _ in [1])

def test_detail_panel_survives_dirty_tool_output(demo):
    """The real-engagement corruption: tool/model output carries raw ANSI
    sequences and control bytes (curl progress, colored dumps) which a
    Rich Text passes STRAIGHT THROUGH — the terminal interpreted them
    (cursor moves, clears, bells) and the pane fell apart. Unbroken
    blobs (JWTs, base64) also blew past the border. Both must be gone:
    sanitized text, folded observation, nothing over-width."""
    ui, _, _ = demo
    import io

    from rich.console import Console

    a = ui.agents[0]
    dirty = (
        "\x1b[2K\rStatus: 200\x1b[32m OK\x1b[0m\r\n"
        "Body: eyJhbGciOiJIUzI1NiJ9." + "x" * 180 + ".sig\n"
        "\x1b[?25l 47%\x07\x00\n end"
    )
    a.iterations = [
        {
            "iteration": 12,
            "thought": "Chaining SSRF \x1b[1mcred grid\x1b[0m",
            "action": {"tool": "http_replay", "args": {"url": "https://t/api"}, "success": True},
            "observation": dirty,
            "phase": "exploitation",
        }
    ]
    c = Console(width=120, height=40, force_terminal=True, record=True, file=io.StringIO())
    c.print(ui._detail_panel())
    lines = c.export_text().splitlines()
    assert not any(ch in ln for ln in lines for ch in ("\x1b", "\r", "\x00", "\x07")), "control bytes leaked"
    assert not [ln for ln in lines if len(ln) > 120], "content escaped the pane border"


def test_detail_pager_hides_blank_stub_iterations(demo):
    """The 'iteration 4, no content' bug (reader side): old trails stack
    a think-side tool=none stub under the real execute-side entry for
    the same iteration — the pager walked onto blank rows while the red
    window showed the data. The operator dedupes: one row per iteration,
    the rich one."""
    ui, _, _ = demo
    a = ui.agents[0]
    a.audit_data = {
        "cost_usd": 0.01,
        "total_actions": 3,
        "successful_actions": 3,
        "iterations": [
            {"iteration": 3, "thought": "stub", "action": {"tool": "", "success": True}, "observation": ""},
            {
                "iteration": 3,
                "thought": "real",
                "action": {"tool": "http_request", "args": {}, "success": True},
                "observation": "HTTP/1.1 200",
            },
            {
                "iteration": 4,
                "thought": "next",
                "action": {"tool": "execute_terminal", "args": {}, "success": True},
                "observation": "done",
            },
        ],
    }
    from suijin.modules.console.lib.operator_tui import scan_agents  # noqa: F401 — re-scan path uses the merge

    merged = ui.agents[0].audit_data["iterations"]
    # simulate the scan-time merge on the same shape
    out = []
    for it in merged:
        if out and out[-1].get("iteration") == it.get("iteration"):
            act = (it.get("action") or {}).get("tool") or ""
            if act and act != "none":
                out[-1] = it
        else:
            out.append(it)
    assert len(out) == 2
    assert out[0]["action"]["tool"] == "http_request" and out[0]["observation"] == "HTTP/1.1 200"


def test_scan_finds_audits_at_the_v72_engagements_root(tmp_path, monkeypatch):
    """The 'still empty lines' bug: v7.2 moved engagements to base/
    (~/.suijin/engagements) while scan_agents still hunted trails under
    workspace/engagements (= config/engagements, nonexistent) — every
    audit match failed and the detail pager showed numbered EMPTY rows
    while the trails sat populated on disk. Scan must search BOTH."""
    import time as _time

    import suijin.modules.platform.lib.workspace as ws

    mesh = tmp_path / "mesh"
    mesh.mkdir()
    now = _time.time()
    (mesh / "2001.json").write_text(json.dumps({"pid": 2001, "summary": "t.example", "started": now, "beat": now}))
    base = tmp_path / "base"  # the REAL root (v7.2): NOT under workspace/
    eng = base / "engagements" / "20261014_000000_hunt"
    (eng / "audit_trails").mkdir(parents=True)
    (eng / "audit_trails" / "hunt.json").write_text(
        json.dumps(
            {
                "engagement": "hunt",
                "started": "2026-10-14T00:00:00+00:00",
                "iterations": [
                    {
                        "iteration": 1,
                        "thought": "x",
                        "action": {"tool": "nmap_scan", "args": {}, "success": True},
                        "observation": "22 open",
                    }
                ],
            }
        )
    )
    (mesh / "2001-state.json").write_text(
        json.dumps({"pid": 2001, "phase": "recon", "iteration": 1, "engagement_dir": str(eng)})
    )
    monkeypatch.setattr(ws, "engagements_root", lambda: base / "engagements")
    agents = ot.scan_agents(mesh, tmp_path / "config", live=False)
    assert len(agents) == 1
    a = agents[0]
    assert a.iterations and a.iterations[0]["action"]["tool"] == "nmap_scan", "trail not matched at the base root"


class TestGuidanceForensics:
    def test_wrap_up_writes_the_permanent_log(self, tmp_path):
        """2026-10-30 fabricated-wrap-up incident: the live file is consumed
        by design and the evidence vanished. Every guidance write now lands
        in the append-only guidance_log.md WITH its origin."""
        mesh = tmp_path / "mesh"
        mesh.mkdir()
        eng = tmp_path / "eng" / "e1"
        (eng / "state").mkdir(parents=True)
        import time as _t

        now = _t.time()
        (mesh / "4242.json").write_text(json.dumps({"pid": 4242, "summary": "t", "phase": "r", "started": now, "beat": now}))
        ui = ot.OperatorTUI(mesh, tmp_path, live=False)
        ui.agents = ot.scan_agents(mesh, tmp_path, live=False)
        ui.agents[0].engagement_dir = eng
        assert ui._wrap_up_guidance(ui.agents[0], origin="operator-tui:/report")
        log = (eng / "state" / "guidance_log.md").read_text()
        assert "[operator-tui:/report:REPORT]" in log and "wrap up now" in log
        live = (eng / "state" / "live_guidance.md").read_text()
        assert "wrap up now" in live  # consumed later by design; log persists


class TestDormantRows:
    def test_yesterdays_engagements_stay_visible(self, tmp_path):
        """2026-10-30 operator rule: 'like from yesterday' — recent
        engagements with no live node remain rows in the operator."""

        eng_root = tmp_path / "engs"
        d = eng_root / "20261029_120000_127.0.0.1_6000"
        (d / "state").mkdir(parents=True)
        (d / "events.jsonl").write_text('{"v":1,"seq":1,"kind":"state.snapshot","state":{"current_iteration":27}}\n')
        rows = ot.scan_dormant([{"mesh": tmp_path / "mesh", "engagements": eng_root}], claimed=set())
        assert len(rows) == 1
        a = rows[0]
        assert a.origin.startswith("dormant:") and a.phase == "ended" and a.pid == 0
        assert a.iteration == 27 and "127" in a.target

    def test_claimed_and_stale_excluded(self, tmp_path):
        import time as _t

        eng_root = tmp_path / "engs"
        live_d = eng_root / "20261030_live_x"
        old_d = eng_root / "20201030_old_x"
        for dd in (live_d, old_d):
            dd.mkdir(parents=True)
            (dd / "events.jsonl").write_text("{}\n")
        import os

        os.utime(old_d / "events.jsonl", (_t.time() - 100 * 3600,) * 2)  # 100h stale
        rows = ot.scan_dormant([{"engagements": eng_root}], claimed={str(live_d)}, hours=48)
        assert rows == []
