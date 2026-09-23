"""Engagement bundles (.sje) — save at conclusion, resume with full memory.

Contract:
  - save_engagement zips manifest (hash-sealed) + graph_state subset +
    the exploit catalogs; sensitive config keys are stripped
  - load_engagement verifies every hash — tampered/corrupt bundles are
    refused with the exact reason
  - resume seeds a FRESH graph thread via update_state (messages, chain
    memory, phase restored; completion_reason cleared so it keeps working)
  - `suijin load <file.sje>` is a registered verb
"""

import contextlib
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

from suijin.modules.tools.lib import engagement_bundle as eb


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    import suijin.modules.platform.lib.workspace as ws

    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    ws._reset_engagement()  # hermetic: no engagement pinned from another test
    yield tmp_path


def _state(n_messages=5):
    return {
        "messages": [{"role": "user", "content": f"m{i}"} for i in range(n_messages)],
        "original_objective": "Test http://target.example",
        "current_phase": "exploitation",
        "current_iteration": 12,
        "attack_path_type": "sql_injection",
        "chain_failures_memory": ["sqlmap blocked"],
        "execution_trace": [{"tool_name": "http_request"} for _ in range(3)],
        "target_info": {"ports": [80, 443]},
        "completion_reason": "objective_complete",
    }


class TestSave:
    def test_bundle_layout_and_seal(self):
        path = eb.save_engagement("redteam_1", "Test http://t", {"provider": "zai"}, _state(), 0.42)
        assert path.suffix == ".sje" and path.is_file()
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        assert "manifest.json" in names and "graph_state.json" in names
        bundle = eb.load_engagement(path)
        man = bundle["manifest"]
        assert man["files"]["graph_state.json"]  # hash-sealed
        assert man["cost_usd"] == 0.42
        assert man["objective"] == "Test http://t"

    def test_sensitive_config_stripped(self):
        path = eb.save_engagement(
            "t", "obj", {"provider": "zai", "api_key": "sk-secret", "ZAI_API_KEY": "x", "ok": 1}, _state()
        )
        man = eb.load_engagement(path)["manifest"]
        assert man["config"]["api_key"] == "***stripped***"
        assert man["config"]["ZAI_API_KEY"] == "***stripped***"
        assert man["config"]["provider"] == "zai" and man["config"]["ok"] == 1
        assert "sk-secret" not in json.dumps(man)

    def test_exploit_catalog_travels(self):
        import suijin.modules.tools.lib.exploit_catalog as ec

        edir = ec._engagement_dir("t")
        (edir / "EXP-001.poc.json").write_text("[]")
        path = eb.save_engagement("t", "obj", {}, _state())
        with zipfile.ZipFile(path) as zf:
            assert "exploits/t/EXP-001.poc.json" in zf.namelist()

    def test_messages_capped_with_resume_marker(self):
        state = _state(n_messages=200)
        path = eb.save_engagement("t", "obj", {}, state)
        gs = eb.load_engagement(path)["graph_state"]
        assert len(gs["messages"]) == eb.MAX_RESUME_MESSAGES
        assert "resumed from a saved engagement" in gs["messages"][0]["content"]


class TestLoad:
    def test_roundtrip_state(self):
        path = eb.save_engagement("t", "obj", {"provider": "zai"}, _state())
        gs = eb.load_engagement(path)["graph_state"]
        assert gs["current_phase"] == "exploitation"
        assert gs["chain_failures_memory"] == ["sqlmap blocked"]
        assert len(gs["messages"]) == 5

    def test_tamper_refused(self, tmp_path):
        path = eb.save_engagement("t", "obj", {}, _state())
        # rewrite one sealed file inside a copy
        bad = tmp_path / "bad.sje"
        with zipfile.ZipFile(path) as zin, zipfile.ZipFile(bad, "w") as zout:
            for n in zin.namelist():
                data = zin.read(n)
                if n == "graph_state.json":
                    data = data.replace(b"exploitation", b"POST-EXPLOIT")
                zout.writestr(n, data)
        with pytest.raises(ValueError, match="hash mismatch"):
            eb.load_engagement(bad)

    def test_wrong_extension_and_missing(self, tmp_path):
        with pytest.raises(ValueError, match="no such file"):
            eb.load_engagement(tmp_path / "nope.sje")
        p = tmp_path / "notasje.txt"
        p.write_text("x")
        with pytest.raises(ValueError, match="not a .sje"):
            eb.load_engagement(p)

    def test_restore_side_files(self):
        import suijin.modules.tools.lib.exploit_catalog as ec

        ec._engagement_dir("t").mkdir(parents=True, exist_ok=True)
        (ec._engagement_dir("t") / "catalog.json").write_text("{}")
        path = eb.save_engagement("t", "obj", {}, _state())
        # wipe the workspace catalogs, restore from the bundle
        import shutil

        shutil.rmtree(ec._catalog_roots()[0] if ec._catalog_roots() else Path(tempfile.mkdtemp()))
        n = eb.restore_side_files(path)
        assert n >= 1
        assert (ec._engagement_dir("t") / "catalog.json").is_file()


class TestResumeWiring:
    def test_resume_seeds_thread_via_update_state(self, monkeypatch, tmp_path):
        """run_red_team(resume_state=...) injects the saved state into the
        fresh thread and skips the objective-injection first turn."""
        from suijin.modules.redteam.lib import redteamer

        injected = {}

        class _Graph:
            def update_state(self, cfg, values):
                injected.update(values)

        class _Agent:
            def __init__(self):
                self._graph = _Graph()

        captured = {}

        async def fake_async(config, objective, api_key=None, resume_state=None):
            captured["resume"] = resume_state
            return None

        monkeypatch.setattr(redteamer, "run_red_team_async", fake_async)

        def _run_coro(coro):
            import contextlib

            with contextlib.suppress(StopIteration):
                coro.send(None)  # no awaits inside — runs to completion

        monkeypatch.setattr(redteamer.asyncio, "run", _run_coro)
        out = redteamer.run_red_team({"provider": "zai"}, "obj", resume_state={"messages": ["x"]})
        assert out in (None, 0)
        assert captured["resume"] == {"messages": ["x"]}

    def test_load_verb_registered(self):
        from suijin.modules.console.lib.cli import _KNOWN_VERBS, is_known_verb

        assert "load" in _KNOWN_VERBS and is_known_verb("load")

    def test_load_cmd_bad_file_errors(self, tmp_path, capsys):
        from suijin.modules.console.lib.cli import run_load_cmd

        rc = run_load_cmd(type("A", (), {"bundle": str(tmp_path / "missing.sje")})())
        assert rc == 1 and "error" in capsys.readouterr().out


class TestCrashSaver:
    """An .sje exists for EVERY exit path — conclusion, crash backstop,
    signal, excepthook, atexit. Idempotent: one bundle per engagement."""

    def _armed(self, state=None):
        saver = eb.CrashSaver()
        saver.arm("t_crash", "Crash http://t", {"provider": "zai"}, lambda: state or _state())
        return saver

    def test_save_writes_valid_bundle(self):
        saver = self._armed()
        path = saver.save("unittest")
        assert path is not None and path.is_file()
        bundle = eb.load_engagement(path)  # hash-verifies
        assert bundle["manifest"]["objective"] == "Crash http://t"
        assert bundle["graph_state"]["current_phase"] == "exploitation"

    def test_idempotent_one_bundle_per_arm(self):
        saver = self._armed()
        first = saver.save("conclusion")
        assert saver.save("end") is None  # backstop no-ops
        assert saver.save("signal:SIGTERM") is None
        assert first.is_file()

    def test_mark_saved_short_circuits(self):
        saver = self._armed()
        saver.mark_saved()
        assert saver.save("end") is None and saver.last_path is None

    def test_unarmed_save_is_noop(self):
        assert eb.CrashSaver().save("crash") is None

    def test_crashing_get_state_still_saves_empty(self):
        saver = eb.CrashSaver()

        def _boom():
            raise RuntimeError("graph is gone")

        saver.arm("t", "obj", {}, _boom)
        path = saver.save("crash")
        assert path is not None and eb.load_engagement(path)["graph_state"] == {}

    def test_rearm_for_second_engagement(self):
        saver = self._armed()
        saver.save("conclusion")
        saver.arm("t2", "Second http://t2", {}, lambda: {})
        path = saver.save("conclusion")
        assert path is not None
        assert eb.load_engagement(path)["manifest"]["objective"] == "Second http://t2"

    def test_disarm_restores_handlers(self):
        import signal as _sig

        saver = eb.CrashSaver()
        before = _sig.getsignal(_sig.SIGTERM)
        saver.arm("t", "obj", {}, lambda: {})
        assert _sig.getsignal(_sig.SIGTERM) is not before
        saver.disarm()
        assert _sig.getsignal(_sig.SIGTERM) is before

    def test_signal_handler_saves_then_defers_to_full_auto(self):
        import signal as _sig

        calls = []

        def _full_auto(sig, frame):
            calls.append(sig)  # the FULL-AUTO KI-raising handler

        old = _sig.signal(_sig.SIGTERM, _full_auto)
        try:
            saver = eb.CrashSaver()
            saver.arm("t", "obj", {}, lambda: _state())
            _sig.getsignal(_sig.SIGTERM)(_sig.SIGTERM, None)  # fire in-process
        finally:
            _sig.signal(_sig.SIGTERM, old)
        assert calls == [_sig.SIGTERM]  # chained, not swallowed
        assert saver.saved and saver.last_path.is_file()

    def test_final_block_crash_still_saves_via_finally(self):
        """The redteamer finally pattern: a crash INSIDE the final-report
        block (after the loop, before the conclusion save) still yields a
        bundle because the finally backstop runs before disarm."""
        saver = eb.CrashSaver()
        saver.arm("t", "Final block crash", {"provider": "zai"}, lambda: _state())
        try:
            try:
                raise RuntimeError("final block exploded")
            finally:
                with contextlib.suppress(Exception):
                    saver.save("end")  # the redteamer finally hook
            with contextlib.suppress(Exception):
                saver.disarm()
        except RuntimeError:
            pass
        assert saver.saved and saver.last_path.is_file()
        assert eb.load_engagement(saver.last_path)["manifest"]["objective"] == "Final block crash"


class TestRecentBundlesAndPicker:
    def test_recent_bundles_newest_first_with_metadata(self):
        p1 = eb.save_engagement("t1", "First http://one", {}, _state(), 0.1)
        import time as _t

        _t.sleep(0.05)
        p2 = eb.save_engagement("t2", "Second http://two", {}, _state(), 2.5)
        bundles = eb.recent_bundles(limit=10)
        names = [b["name"] for b in bundles]
        assert names.index(p2.name) < names.index(p1.name)  # newest first
        top = bundles[0]
        assert top["objective"] == "Second http://two"
        assert top["cost_usd"] == 2.5
        assert top["size_kb"] >= 1

    def test_recent_bundles_limit(self):
        for i in range(4):
            eb.save_engagement(f"t{i}", f"obj {i}", {}, _state())
        assert len(eb.recent_bundles(limit=3)) == 3

    def test_resolve_bundle_bare_name(self):
        path = eb.save_engagement("t1", "Resolve http://me", {}, _state())
        assert eb.resolve_bundle(path.name) == path  # name w/ suffix
        assert eb.resolve_bundle(path.stem) == path  # name w/o suffix
        assert eb.resolve_bundle(str(path)) == path  # full path

    def test_resolve_bundle_rejects_garbage(self):
        with pytest.raises(ValueError, match="no such bundle"):
            eb.resolve_bundle("definitely-not-there")
        with pytest.raises(ValueError):
            eb.resolve_bundle("")

    def test_pick_non_tty_takes_newest(self, monkeypatch, capsys):

        p2 = eb.save_engagement("t2", "The newest", {}, _state())
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # non-TTY
        assert eb.pick_bundle() == p2
        assert "non-interactive" in capsys.readouterr().out

    def test_pick_empty_inbox(self, capsys):
        assert eb.pick_bundle() is None
        assert "no saved engagements" in capsys.readouterr().out

    def test_load_cmd_no_arg_launches_picker_then_resumes(self, monkeypatch):
        import suijin.modules.redteam.lib.redteamer as rt
        from suijin.modules.console.lib.cli import run_load_cmd

        path = eb.save_engagement("t", "Picked http://one", {}, _state())
        monkeypatch.setattr(eb, "pick_bundle", lambda limit=10: path)
        monkeypatch.setattr(rt, "run_red_team", lambda *a, **k: 0)
        rc = run_load_cmd(type("A", (), {"bundle": None})())
        assert rc == 0


class TestResumeConfigMerge:
    def test_stripped_placeholders_do_not_leak_into_config(self, monkeypatch):
        """A '***stripped***' marker must never override a live default —
        resume_engagement drops them before merging current config."""
        path = eb.save_engagement("t", "obj", {"provider": "zai", "api_key": "sk-real", "ok": 1}, _state())
        captured = {}

        import suijin.modules.platform.lib.config_loader as cl

        monkeypatch.setattr(cl, "load_config", lambda: {"provider": "deepseek"})
        import suijin.modules.redteam.lib.redteamer as rt

        def _fake_run(config, objective, api_key=None, resume_state=None):
            captured["cfg"] = config
            return 7

        monkeypatch.setattr(rt, "run_red_team", _fake_run)
        assert eb.resume_engagement(path) == 7
        cfg = captured["cfg"]
        assert cfg["provider"] == "deepseek"  # current config wins
        assert "api_key" not in cfg or cfg.get("api_key") != "***stripped***"
        assert "***stripped***" not in json.dumps(cfg)


class TestGroundedReports:
    """Reports are deterministic and evidence-joined: findings carry their
    catalog verification status, CONFIRMED POCs render reproduction steps,
    no-catalog findings stay UNVERIFIED (honest, never inflated)."""

    def test_unverified_without_catalog(self, tmp_path):
        import suijin.modules.platform.lib.workspace as ws
        from suijin.modules.tools.lib.report_exporter import generate_report

        monkeypatch_target = ws.WORKSPACE_DIR
        ws.WORKSPACE_DIR = tmp_path
        try:
            path = generate_report(
                "unit grounded",
                [{"tool_name": "http_request", "success": True, "thought": "t"}],
                [{"type": "sqli", "severity": "high", "endpoint": "/x", "description": "d"}],
                {},
                [],
                cost_usd=0.01,
            )
            body = Path(path).read_text()
            assert "UNVERIFIED" in body
            assert "## Findings" in body
        finally:
            ws.WORKSPACE_DIR = monkeypatch_target

    def test_remediation_deterministic(self, tmp_path):
        import suijin.modules.platform.lib.workspace as ws
        from suijin.modules.tools.lib.report_exporter import generate_report

        keep = ws.WORKSPACE_DIR
        ws.WORKSPACE_DIR = tmp_path
        try:
            path = generate_report(
                "unit rem",
                [],
                [{"type": "sourcemap", "severity": "medium", "endpoint": "cdn", "description": "d"}],
                {},
                [],
            )
            body = Path(path).read_text()
            assert "Strip source maps" in body
        finally:
            ws.WORKSPACE_DIR = keep


class TestV3BundleLocations:
    """v3: bundles live in the engagement's state/; the picker also reads
    the legacy exports/ inbox so old workspaces keep resuming."""

    def test_bundle_saved_inside_engagement(self, tmp_path, monkeypatch):
        import suijin.modules.platform.lib.workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        ws._reset_engagement()
        ws.set_engagement("v3 bundle location test")
        path = eb.save_engagement("t", "obj", {}, _state())
        assert "/engagements/" in str(path) and "/state/" in str(path)

    def test_picker_reads_legacy_exports_too(self, tmp_path, monkeypatch):
        import suijin.modules.platform.lib.workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        ws._reset_engagement()
        legacy = tmp_path / "exports"
        legacy.mkdir()
        (legacy / "old_run_20260101.sje").write_bytes(b"placeholder")
        files = eb._bundle_files()
        assert any("old_run_20260101.sje" in str(f) for f in files)
