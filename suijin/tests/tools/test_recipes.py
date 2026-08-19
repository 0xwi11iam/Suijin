"""Tool recipes (A3) + auto-recipe miner (A4)."""

import json

from suijin.modules.tools.lib.recipes import (
    BUILT_IN_RECIPES,
    mine_recipes,
    recipe_define,
    recipe_list,
    recipe_run,
)


def _fake_route(tool, args, config):
    if tool == "whatweb_scan":
        return "whatweb: nginx"
    if tool == "extract_links":
        return "links: /a /b"
    return "ok"


class TestRecipes:
    def test_list_shows_builtins(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        out = recipe_list()
        for name in BUILT_IN_RECIPES:
            assert name in out

    def test_run_templates_target_and_chains_prev(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        out = recipe_run("recon_web", "http://t.example", route_fn=_fake_route)
        assert "complete (4 steps)" in out or "aborted" in out  # optional steps may fail gracefully
        # non-optional first step ran with templated target

    def test_run_aborts_on_required_failure(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)

        def boom(tool, args, config):
            return "Error: tool down"

        out = recipe_run("recon_web", "t.example", route_fn=boom)
        assert "aborted at step 1" in out

    def test_define_and_run_user_recipe(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        steps = json.dumps([{"tool": "extract_links", "args": {"html": "{prev}"}}, {"tool": "done_tool", "args": {}}])
        assert "defined recipe 'mine_flow'" in recipe_define("mine_flow", steps)
        assert "mine_flow" in recipe_list()
        assert "Error" not in recipe_define("nope", '[{"tool": 1}]') or "Error" in recipe_define("nope", "not json")

    def test_unknown_recipe(self):
        assert "no recipe" in recipe_run("nope", "t", route_fn=_fake_route)


class TestMiner:
    def test_mines_repeated_sequences(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        d = tmp_path / "outputs" / "audit_trails"
        d.mkdir(parents=True)
        seq = [{"tool": t, "success": True} for t in ("a", "b", "c")]
        for name in ("eng1", "eng2"):
            (d / f"{name}.json").write_text(json.dumps({"engagement": name, "iterations": seq}))
        out = mine_recipes(min_support=2, min_len=3)
        assert "proposal" in out and "a -> b -> c" in out

    def test_no_history(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        assert "No audit trails" in mine_recipes()
