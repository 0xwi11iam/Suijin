"""Environment contract + pack-root alignment (2026-10-20 round 4).

The model learned its jails by bumping into them (the documented
3-iteration sandbox-warning dance) because the prompt's entire
environmental contract was one macOS line. These pins hold the
contract, the v7.2 pack roots, and the coherent /tmp messaging.
"""

import re
from pathlib import Path


class TestEnvironmentContract:
    def test_prompt_carries_the_machine_contract(self):
        from suijin.modules.agent.lib.prompts.base import build_agent_system_prompt

        p = build_agent_system_prompt(
            {"current_phase": "informational", "original_objective": "test https://t.example"}
        )
        assert "ENVIRONMENT — the machine contract" in p
        assert "NOT in a container" in p
        assert "$TMPDIR" in p and "/var/tmp" in p
        assert "background=true" in p

    def test_tmp_warning_never_fires_on_var_tmp(self):
        from suijin.modules.tools.lib.terminal import execute_terminal  # noqa: F401 — module import for source check

        src = Path(execute_terminal.__module__.replace(".", "/") + ".py")
        # find the module file via import instead of guessing paths
        import suijin.modules.tools.lib.terminal as term_mod

        src = Path(term_mod.__file__)
        body = src.read_text()
        # the contradictory substring check must be gone
        assert 'if "/tmp" in cmd' not in body
        assert re.search(r'\(\?<!var\)/tmp", cmd', body)  # lookbehind guard present


class TestPackRootsV72:
    def test_default_roots_include_workspace_and_legacy(self):
        from suijin.modules.loader import _default_pack_roots

        roots = _default_pack_roots()
        assert roots[0].name == "modules" and roots[0].parent.name == "suijin"  # vendored first
        joined = [str(r) for r in roots]
        assert any("modules" in j and ".suijin" in j for j in joined[1:])  # user root present
        # legacy fallback survives as a READ root
        assert any(
            j.endswith(Path.home().joinpath(".suijin", "modules").name) and str(Path.home()) in j for j in joined
        )

    def test_write_tool_lands_where_loader_scans(self, tmp_path, monkeypatch):
        from suijin.modules import loader

        user_root = tmp_path / "user_modules"
        monkeypatch.setattr(loader, "PACK_ROOTS", [Path("/nonexistent-vendored"), user_root])
        from suijin.modules.tools.lib.self_improve import write_tool

        code = "def env_probe_tool(**kwargs):\n    return 'ok'\n"
        out = write_tool("env_probe_tool", code)
        assert "REGISTERED" in out
        assert (user_root / "env_probe_tool" / "main.py").exists()
