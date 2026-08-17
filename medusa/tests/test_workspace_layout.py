"""Workspace layout tests — one canonical <repo>/medusa_agent/.

The contract (README): agent artifacts live in the ROOT medusa_agent/,
medusa/medusa_agent is a symlink -> ../medusa_agent, and nothing writes the
agent workspace outside the root. ensure_workspace_layout() auto-repairs a
legacy real inner dir by merging its contents up.
"""

import os
from pathlib import Path

from medusa.tools import workspace as ws


def _make(tmp_path):
    base = tmp_path / "medusa"
    base.mkdir()
    root = tmp_path / "medusa_agent"
    root.mkdir()
    return base, root


class TestEnsureWorkspaceLayout:
    def test_merges_inner_into_root_and_symlinks(self, tmp_path):
        base, root = _make(tmp_path)
        inner = base / "medusa_agent"
        (inner / "reports").mkdir(parents=True)
        (inner / "reports" / "r.md").write_text("report")
        (inner / "SOUL.md").write_text("soul (inner wins)")
        (root / "outputs").mkdir()
        (root / "SOUL.md").write_text("old root copy")

        assert ws.ensure_workspace_layout(base_dir=base, workspace_dir=root) is True

        # legacy data now lives in the root workspace
        assert (root / "reports" / "r.md").read_text() == "report"
        assert (root / "SOUL.md").read_text() == "soul (inner wins)"
        assert (root / "outputs").is_dir()
        # inner path replaced by the symlink
        assert inner.is_symlink()
        assert os.readlink(inner) == "../medusa_agent"
        assert (inner / "reports" / "r.md").read_text() == "report"  # readable through it

    def test_idempotent_when_symlink_exists(self, tmp_path):
        base, root = _make(tmp_path)
        ws.ensure_workspace_layout(base_dir=base, workspace_dir=root)
        assert ws.ensure_workspace_layout(base_dir=base, workspace_dir=root) is False
        assert (base / "medusa_agent").is_symlink()

    def test_creates_symlink_when_inner_absent(self, tmp_path):
        base, root = _make(tmp_path)
        assert ws.ensure_workspace_layout(base_dir=base, workspace_dir=root) is True
        assert (base / "medusa_agent").is_symlink()

    def test_nested_dir_collision_merges_recursively(self, tmp_path):
        base, root = _make(tmp_path)
        inner = base / "medusa_agent"
        (inner / "outputs").mkdir(parents=True)
        (inner / "outputs" / "job.log").write_text("log")
        (root / "outputs").mkdir()
        (root / "outputs" / "keep.txt").write_text("keep")

        ws.ensure_workspace_layout(base_dir=base, workspace_dir=root)

        assert (root / "outputs" / "job.log").read_text() == "log"
        assert (root / "outputs" / "keep.txt").read_text() == "keep"
        assert inner.is_symlink()


class TestCanonicalLayout:
    def test_repo_layout_is_canonical(self):
        # Repair first so the assertion holds regardless of import order.
        ws.ensure_workspace_layout()
        inner = ws.PROJECT_DIR / "medusa" / "medusa_agent"
        assert ws.WORKSPACE_DIR == ws.PROJECT_DIR / "medusa_agent"
        assert inner.is_symlink() or not inner.exists()

    def test_sandbox_inside_workspace(self):
        from medusa.infra import job_runner

        wd = Path(job_runner.get_sandbox_workdir())
        assert str(wd).startswith(str(ws.WORKSPACE_DIR))

    def test_report_default_paths_anchored(self, tmp_path, monkeypatch):
        from medusa.tools import burp_export, html_report

        monkeypatch.setattr(burp_export, "WORKSPACE_DIR", tmp_path)
        p = burp_export.export_burp_xml([{"finding_type": "xss", "description": "d"}])
        assert str(p).startswith(str(tmp_path / "reports"))
        monkeypatch.setattr(html_report, "WORKSPACE_DIR", tmp_path)
        p = html_report.export_html([{"severity": "high", "type": "xss", "endpoint": "/"}], "eng")
        assert str(p).startswith(str(tmp_path / "reports"))

    def test_tool_dirs_point_at_root(self):
        from medusa.tools import audit_trail, report_exporter, session_replay

        for d in (audit_trail.AUDIT_DIR, report_exporter.REPORTS_DIR, session_replay.REPLAY_DIR):
            assert str(d).startswith(str(ws.WORKSPACE_DIR)), d
