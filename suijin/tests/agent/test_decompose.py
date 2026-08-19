"""Objective decomposer (A5)."""

import json

from suijin.modules.agent.lib.decompose import decompose, render


class TestDecompose:
    def test_llm_plan(self):
        async def gen(messages, config=None, **kw):
            return json.dumps({"subtasks": [{"id": 1, "task": "scan", "depends_on": [], "why": "surface"}]})

        plan = decompose("own the lab", generate_fn=gen)
        assert plan["source"] == "llm" and plan["subtasks"][0]["task"] == "scan"

    def test_heuristic_fallback_on_error(self):
        async def gen(messages, config=None, **kw):
            return "Error: no key"

        plan = decompose("web app test", generate_fn=gen)
        assert plan["source"] == "heuristic" and len(plan["subtasks"]) >= 4

    def test_heuristic_no_llm(self):
        plan = decompose("internal network sweep")
        assert plan["source"] == "heuristic"
        tasks = " ".join(st["task"].lower() for st in plan["subtasks"])
        assert "recon" in tasks and "report" in tasks

    def test_web_objective_gets_web_step(self):
        plan = decompose("test the api at http://t.example")
        assert any("web" in st["task"].lower() or "api" in st["task"].lower() for st in plan["subtasks"])

    def test_empty(self):
        assert decompose("") == {"subtasks": [], "source": "empty"}

    def test_render(self):
        out = render(decompose("x"))
        assert "plan (heuristic):" in out and "[1]" in out
