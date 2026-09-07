"""The diet — per-turn token cost regressions.

Measured before: ~16.5k input tokens/turn steady state (catalog 17.6k
chars + 24k raw-result embed + doctrine duplication); a fireteam burst
shipped ~22.4k chars of tool list per subagent step (~390k tokens per
burst). These tests pin the diet: catalog one-liners, byte-stable prompt
prefix (prefix-cache friendly), head+tail offload digests at 8k, and
task-scoped subagent references.
"""


class TestCatalogDiet:
    def test_catalog_under_8k_chars(self):
        from suijin.modules.agent.lib.prompts.tool_registry import build_tool_catalog_prompt

        for phase in ("informational", "exploitation", "post_exploitation"):
            c = build_tool_catalog_prompt(phase)
            assert len(c) < 8_000, f"{phase}: catalog is {len(c)} chars (was 17.6k — the diet regressed)"
            assert "background" in c.lower()  # the long-running discipline still rides

    def test_every_tool_still_listed_after_diet(self):
        from suijin.modules.agent.lib.prompts.tool_registry import _ALL_TOOLS, TOOL_REGISTRY, build_tool_catalog_prompt

        c = build_tool_catalog_prompt("informational")
        documented = {n for n in _ALL_TOOLS if TOOL_REGISTRY.get(n)}  # registry-described tools
        for name in documented:
            assert name in c, f"{name} vanished from the catalog in the diet"


class TestPrefixStability:
    def test_system_prompt_is_byte_stable_across_turns(self):
        """Prefix-cache friendliness: same state in, byte-identical static
        head out — any per-turn drift in the first N chars kills provider
        prefix caching for the whole engagement."""
        from suijin.modules.agent.lib.prompts.base import build_agent_system_prompt

        st = {
            "objective": "pentest http://t.local",
            "original_objective": "pentest http://t.local",
            "current_phase": "exploitation",
            "attack_path_type": "sqli",
            "target_info": {"endpoints": ["/a", "/b"]},
            "_run_config": {"mode_deploy_subagent": True},
        }
        a = build_agent_system_prompt(st)
        b = build_agent_system_prompt(dict(st))
        assert a == b
        # and the first 4k chars (the cacheable head) are stable even when
        # the dynamic TAIL (state context) changes
        st2 = dict(st, target_info={"endpoints": ["/c", "/d", "/e"], "credentials": ["x"]})
        c = build_agent_system_prompt(st2)
        assert a[:4_000] == c[:4_000]


class TestOffloadDigest:
    def test_threshold_is_8k_with_head_tail_digest(self, tmp_path, monkeypatch):
        import suijin.modules.platform.lib.infra.output_offload as oo
        import suijin.modules.platform.lib.infra.tool_offload_policy as pol
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        assert pol.OFFLOAD_THRESHOLD == 8_000
        big = "HEAD-MARKER\n" + "x" * 20_000 + "\nTAIL-MARKER\n"
        summary, offloaded = oo.maybe_offload("nmap_scan", big)
        assert offloaded
        assert "HEAD-MARKER" in summary and "TAIL-MARKER" in summary  # head+tail survive
        assert len(summary) < 2_200  # the digest is bounded
        small = "y" * 7_999
        out2, off2 = oo.maybe_offload("nmap_scan", small)
        assert not off2 and out2 == small


class TestSubagentScope:
    def test_scoped_reference_beats_full_and_keeps_task_tools(self):
        from suijin.modules.agent.lib.nodes.subagent_node import _scope_tool_reference

        lines = ["## TOOLS"]
        lines += [
            f"- {t}(args): {d}"
            for t, d in [
                ("http_request", "web url request"),
                ("nmap_scan", "network port scan"),
                ("sqlmap_scan", "sqli injection exploit"),
                ("execute_terminal", "shell command run"),
                ("blue_block", "blue defense block"),
                ("generate_report", "report export document"),
                ("write_note", "note record documentation"),
                ("msf_run", "metasploit exploit framework"),
            ]
        ]
        full = "\n".join(lines)
        scoped = _scope_tool_reference(full, "test the /login endpoint for sqli injection")
        assert len(scoped) < len(full) * 0.75  # meaningful cut
        assert "http_request" in scoped and "sqlmap_scan" in scoped and "write_note" in scoped
        assert "blue_block" not in scoped and "generate_report" not in scoped

    def test_no_signal_task_gets_the_full_reference(self):
        from suijin.modules.agent.lib.nodes.subagent_node import _scope_tool_reference

        full = "\n".join(f"- tool_{i}(a): does thing {i}" for i in range(40))
        assert _scope_tool_reference(full, "") == full or _scope_tool_reference(full, "zzz qqq vvv") == full
