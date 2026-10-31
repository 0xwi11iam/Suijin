"""Environment awareness — the agent KNOWS where it runs (2026-10-31)."""

import suijin.modules.agent.lib.environment as env


class TestDetection:
    def test_host_here(self):
        assert env.in_box() is False  # the test machine is the host

    def test_marker_detection(self, tmp_path, monkeypatch):
        monkeypatch.setattr(env.Path, "is_file", lambda self: str(self) == "/.dockerenv", raising=False)
        monkeypatch.setattr(env.Path, "is_dir", lambda self: False, raising=False)
        monkeypatch.delenv("SUIJIN_BOX", raising=False)
        assert env.in_box() is True

    def test_env_var_detection(self, monkeypatch):
        monkeypatch.setattr(env.Path, "is_file", lambda self: False, raising=False)
        monkeypatch.setattr(env.Path, "is_dir", lambda self: False, raising=False)
        monkeypatch.setenv("SUIJIN_BOX", "suijin-box")
        assert env.in_box() is True


class TestContracts:
    def test_box_contract_says_box(self):
        c = env.box_environment_contract()
        assert "INSIDE A BOX" in c and "/tmp" in c and "request_auth" in c

    def test_host_contract_says_host(self):
        c = env.host_environment_contract()
        assert "local access ON" in c.lower() or "LOCAL ACCESS" in c

    def test_general_tail_in_both(self):
        assert "background=true" in env.box_environment_contract()
        assert "background=true" in env.host_environment_contract()

    def test_prompt_renders_the_truth(self, monkeypatch):
        monkeypatch.setattr(env, "in_box", lambda: True)
        from suijin.modules.agent.lib.prompts.base import build_agent_system_prompt

        p = build_agent_system_prompt({"objective": "x"})
        assert "INSIDE A BOX" in p and "macOS Terminal Proficiency" in p
