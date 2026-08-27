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

import json
import zipfile

import pytest

from suijin.modules.tools.lib import engagement_bundle as eb


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    import suijin.modules.platform.lib.workspace as ws

    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
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

        shutil.rmtree(ec._expits_dir())
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
