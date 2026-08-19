"""Phase 3 — recommended tier: providers, redteam, blueteam, knowledge, ops.

The full OS boot: 7 core + 5 recommended modules in one DAG, console
hooks populated by mode modules, disable-means-disappear at tier level.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MODULES = REPO / "suijin" / "modules"


def boot_all(tmp_path):
    from suijin.kernel import controller

    return controller.boot(module_roots=[MODULES], workspace=tmp_path, quiet=True)


class TestRecommendedTier:
    def test_all_modules_boot(self, tmp_path):
        ctx, report = boot_all(tmp_path)
        order_ids = [u.id for u in report.boot_order]
        expected = {
            "platform",
            "tools",
            "agent.graph",
            "agent.nodes",
            "agent.memory",
            "agent",
            "console",  # core
            "providers",
            "redteam",
            "blueteam",
            "knowledge",
            "ops",  # recommended
        }
        assert expected <= set(order_ids), f"missing: {expected - set(order_ids)}"
        assert not report.aborted
        ctx.shutdown()

    def test_dependency_ordering(self, tmp_path):
        ctx, report = boot_all(tmp_path)
        order_ids = [u.id for u in report.boot_order]
        # providers after platform; redteam after agent AND providers
        assert order_ids.index("platform") < order_ids.index("providers")
        assert order_ids.index("agent") < order_ids.index("redteam")
        assert order_ids.index("providers") < order_ids.index("redteam")
        assert order_ids.index("console") < order_ids.index("redteam")
        ctx.shutdown()

    def test_console_menu_populated(self, tmp_path):
        ctx, _ = boot_all(tmp_path)
        hooks = ctx.service("console_hooks")
        menu_ids = [e["id"] for e in hooks.menu()]
        assert menu_ids[:3] == ["redteam", "blueteam", "ops"]  # order field honored
        ctx.shutdown()

    def test_disable_redteam_removes_it_everywhere(self, tmp_path):
        """THE proof at tier level: no redteam => no menu entry, no service,
        no verbs — while everything else boots."""
        # boot with the redteam manifest removed from the tree
        import shutil

        from suijin.kernel import controller

        tree = tmp_path / "modules"
        shutil.copytree(MODULES, tree, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.rmtree(tree / "redteam")
        ctx, report = controller.boot(module_roots=[tree], workspace=tmp_path / "ws", quiet=True)
        assert "redteam" not in report.bootable
        hooks = ctx.service("console_hooks")
        assert [e["id"] for e in hooks.menu()] == ["blueteam", "ops"]
        assert not ctx.has_service("mode.red")
        # blue team + ops unaffected
        assert ctx.has_service("mode.blue")
        ctx.shutdown()

    def test_llm_service_superseded_by_providers(self, tmp_path):
        ctx, _ = boot_all(tmp_path)
        import suijin.tools.providers as p

        # providers' registration won: same module object, one accumulator
        assert ctx.service("llm") is p.generate
        assert ctx.service("llm.failover") is p.generate_with_failover
        ctx.shutdown()


class TestQuietBootWithRecommended:
    def test_healthy_full_boot_is_silent(self, tmp_path, capsys):
        ctx, report = boot_all(tmp_path)
        assert capsys.readouterr().out == ""  # quiet: healthy => silent
        assert not report.skipped and not report.quarantined
        ctx.shutdown()
