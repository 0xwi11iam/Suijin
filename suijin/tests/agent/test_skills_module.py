"""Skills module — drop-in markdown boots into the agent's prompt.

Contract: any .md under suijin/skills/ (or a scanned root) appears in
build_agent_system_prompt() output. Oversized files are skipped and
reported, never break the prompt. This is the gate that makes 'drop a
file and reboot' real.
"""

from pathlib import Path

from suijin.modules.skills.entry import MAX_FILE_BYTES, scan_drop_skills


class TestScan:
    def test_dropped_markdown_loads(self, tmp_path):
        (tmp_path / "sqli-playbook.md").write_text("## SQLi playbook\nAlways check the KG first.")
        text, skipped = scan_drop_skills([tmp_path])
        assert "SQLi playbook" in text
        assert "sqli playbook" in text.lower()
        assert skipped == []

    def test_oversized_file_skipped(self, tmp_path):
        (tmp_path / "huge.md").write_text("x" * (MAX_FILE_BYTES + 1))
        text, skipped = scan_drop_skills([tmp_path])
        assert "huge.md" in skipped and "xxx" not in text

    def test_skip_marker_dormant(self, tmp_path):
        (tmp_path / "draft.md").write_text("<!-- skip -->\ndraft content")
        text, _ = scan_drop_skills([tmp_path])
        assert "draft content" not in text

    def test_bundled_example_ships(self):
        # the shipped example must actually be in the prompt
        text, _ = scan_drop_skills()
        assert "drop-in skill" in text.lower()


class TestBootGate:
    def test_drop_file_boot_appears_in_prompt(self, tmp_path, monkeypatch):
        """THE gate: write a markdown file, build the real system prompt,
        assert the skill is in it."""
        import suijin.modules.skills.entry as entry

        (tmp_path / "fresh-skill.md").write_text("## Fresh skill\nUse cors_check before any CORS claim.")
        monkeypatch.setattr(entry, "_drop_roots", lambda: [tmp_path])

        from suijin.modules.agent.lib.prompts.base import build_agent_system_prompt

        prompt = build_agent_system_prompt({"objective": "gate test", "target_info": {}})
        assert "## SKILLS (drop-in)" in prompt
        assert "Use cors_check before any CORS claim." in prompt

    def test_module_boots_and_registers(self, tmp_path):
        from suijin.kernel import controller

        ctx, report = controller.boot(
            module_roots=[Path(__file__).resolve().parents[3] / "suijin" / "modules"], workspace=tmp_path, quiet=True
        )
        assert any(u.id == "skills" for u in report.boot_order)
        docs = ctx.service("skills.docs")
        assert isinstance(docs, str) and len(docs) > 0
        ctx.shutdown()
