"""Prompt budget + tool-parity gates.

THE invariant: the agent ALWAYS sees every registered tool and the
universal call format — in under 10k tokens of static prompt.
"""

from pathlib import Path

MODULES = Path(__file__).resolve().parents[3] / "suijin" / "modules"
BUDGET_TOKENS = 10_000


def _boot():
    from suijin.kernel import controller

    ctx, _ = controller.boot(module_roots=[MODULES], workspace=Path("/tmp/promptbudget"), quiet=True)
    return ctx


class TestToolParity:
    def test_every_registered_tool_is_in_the_reference(self):
        ctx = _boot()
        try:
            ref = ctx.tool_reference()
            missing = [n for n in ctx.tool_names() if f"- {n}(" not in ref and f"- {n}()" not in ref]
            assert not missing, f"registered but invisible to the agent: {missing[:10]}"
        finally:
            ctx.shutdown()

    def test_catalog_lists_every_registered_tool(self):
        from suijin.modules.loader import discover_modules

        discover_modules()
        from suijin.modules.tools.lib import dispatch

        ctx = _boot()
        try:
            catalog = dispatch.get_tool_catalog()
            missing = [n for n in ctx.tool_names() if n not in catalog]
            assert not missing, f"catalog is missing: {missing[:10]}"
        finally:
            ctx.shutdown()

    def test_universal_call_format_present(self):
        from suijin.modules.loader import discover_modules

        discover_modules()
        from suijin.modules.tools.lib import dispatch

        cat = dispatch.get_tool_catalog()
        assert "HOW TO CALL ANY TOOL" in cat and '"action": "use_tool"' in cat
        # tool-not-found rule present
        assert "ask_operator" in cat


class TestPromptBudget:
    def test_full_system_prompt_under_budget(self):
        from suijin.modules.agent.lib.prompts.base import build_agent_system_prompt
        from suijin.modules.loader import discover_modules

        discover_modules()
        ctx = _boot()
        try:
            prompt = build_agent_system_prompt({"objective": "budget test", "target_info": {}})
        finally:
            ctx.shutdown()
        tokens = len(prompt) // 4
        print(f"\nFULL SYSTEM PROMPT: {len(prompt):,} chars ≈ {tokens:,} tokens")
        assert tokens < BUDGET_TOKENS, f"static prompt is {tokens:,} tokens (budget {BUDGET_TOKENS:,})"

    def test_fallback_reference_before_any_boot(self):
        """The manifest fallback must ALSO list tools — the agent is
        never left blind, even pre-boot."""
        import sys

        from suijin.modules.tools.lib import dispatch

        # force the fallback by pretending there is no last context
        saved = sys.modules.get("suijin.kernel.controller").__dict__.get("_LAST_CONTEXT")
        sys.modules["suijin.kernel.controller"]._LAST_CONTEXT = None
        try:
            ref = dispatch._manifest_reference()
        finally:
            sys.modules["suijin.kernel.controller"]._LAST_CONTEXT = saved
        assert "nmap" in ref and "encode_text(" in ref
