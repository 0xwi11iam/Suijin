"""Workspace layout tests — one canonical <repo>/suijin_agent/.

The contract (README): agent artifacts live in the ROOT suijin_agent/,
suijin/suijin_agent is a symlink -> ../suijin_agent, and nothing writes the
agent workspace outside the root. ensure_workspace_layout() auto-repairs a
legacy real inner dir by merging its contents up.
"""

import os
from pathlib import Path

from suijin.modules.platform.lib import workspace as ws


def _make(tmp_path):
    base = tmp_path / "suijin"
    base.mkdir()
    root = tmp_path / "suijin_agent"
    root.mkdir()
    return base, root


class TestEnsureWorkspaceLayout:
    def test_merges_inner_into_root_and_symlinks(self, tmp_path):
        base, root = _make(tmp_path)
        inner = base / "suijin_agent"
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
        assert os.readlink(inner) == "../suijin_agent"
        assert (inner / "reports" / "r.md").read_text() == "report"  # readable through it

    def test_idempotent_when_symlink_exists(self, tmp_path):
        base, root = _make(tmp_path)
        ws.ensure_workspace_layout(base_dir=base, workspace_dir=root)
        assert ws.ensure_workspace_layout(base_dir=base, workspace_dir=root) is False
        assert (base / "suijin_agent").is_symlink()

    def test_creates_symlink_when_inner_absent(self, tmp_path):
        base, root = _make(tmp_path)
        assert ws.ensure_workspace_layout(base_dir=base, workspace_dir=root) is True
        assert (base / "suijin_agent").is_symlink()

    def test_nested_dir_collision_merges_recursively(self, tmp_path):
        base, root = _make(tmp_path)
        inner = base / "suijin_agent"
        (inner / "outputs").mkdir(parents=True)
        (inner / "outputs" / "job.log").write_text("log")
        (root / "outputs").mkdir()
        (root / "outputs" / "keep.txt").write_text("keep")

        ws.ensure_workspace_layout(base_dir=base, workspace_dir=root)

        assert (root / "outputs" / "job.log").read_text() == "log"
        assert (root / "outputs" / "keep.txt").read_text() == "keep"
        assert inner.is_symlink()


class TestCanonicalLayout:
    def test_repo_layout_is_canonical(self, monkeypatch):
        # v5.3: WORKSPACE_DIR resolves durable (~/.suijin/workspace) when
        # present — force the repo-local resolution for layout mechanics.
        monkeypatch.setattr(ws, "WORKSPACE_DIR", ws.PROJECT_DIR / "suijin_agent")
        # Repair first so the assertion holds regardless of import order.
        ws.ensure_workspace_layout()
        inner = ws.PROJECT_DIR / "suijin" / "suijin_agent"
        assert ws.WORKSPACE_DIR == ws.PROJECT_DIR / "suijin_agent"
        assert inner.is_symlink() or not inner.exists()

    def test_workspace_resolution_order(self, monkeypatch, tmp_path):
        """env override > durable ~/.suijin/workspace > repo-local."""
        import pathlib

        # isolate: a scratch PROJECT_DIR so an installed-layout symlink at
        # the real repo-local path cannot short-circuit resolution
        scratch = tmp_path / "repo"
        (scratch / "suijin").mkdir(parents=True)
        monkeypatch.setattr(ws, "PROJECT_DIR", scratch)
        monkeypatch.delenv("SUIJIN_WORKSPACE", raising=False)
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: fake_home))
        # repo-local: no env, no durable dir at the fake home
        assert ws._resolve_workspace() == scratch / "suijin_agent"
        # durable: create it at the fake home -> it wins over repo-local
        durable = fake_home / ".suijin" / "workspace"
        durable.mkdir(parents=True)
        assert ws._resolve_workspace() == durable
        # env: explicit override beats everything
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(tmp_path / "explicit"))
        assert ws._resolve_workspace() == tmp_path / "explicit"
        # dangling committed symlink (CI incident): repo/suijin_agent ->
        # an absolute path that does not exist must NOT leak into mkdirs;
        # it falls back to the HOME-based durable home
        monkeypatch.delenv("SUIJIN_WORKSPACE")

        (scratch / "suijin_agent").symlink_to("/definitely/not/anywhere")
        assert ws._resolve_workspace() == durable

    def test_sandbox_inside_workspace(self):
        from suijin.modules.platform.lib.infra import job_runner

        wd = Path(job_runner.get_sandbox_workdir())
        assert str(wd).startswith(str(ws.WORKSPACE_DIR))

    def test_report_default_paths_anchored(self, tmp_path, monkeypatch):
        from suijin.modules.tools.lib import burp_export, html_report

        monkeypatch.setattr(burp_export, "WORKSPACE_DIR", tmp_path)
        p = burp_export.export_burp_xml([{"finding_type": "xss", "description": "d"}])
        assert str(p).startswith(str(tmp_path / "reports"))
        monkeypatch.setattr(html_report, "WORKSPACE_DIR", tmp_path)
        p = html_report.export_html([{"severity": "high", "type": "xss", "endpoint": "/"}], "eng")
        assert str(p).startswith(str(tmp_path / "reports"))

    def test_tool_dirs_point_at_root(self):
        from suijin.modules.tools.lib import audit_trail, report_exporter, session_replay

        for d in (audit_trail.AUDIT_DIR, report_exporter.REPORTS_DIR, session_replay.REPLAY_DIR):
            assert str(d).startswith(str(ws.WORKSPACE_DIR)), d


class TestRenameMigration:
    """Medusa -> Suijin rename: legacy medusa_agent data must carry over."""

    def test_legacy_root_renamed_when_new_absent(self, tmp_path):
        base = tmp_path / "suijin"
        base.mkdir()
        legacy = tmp_path / "medusa_agent"
        (legacy / "reports").mkdir(parents=True)
        (legacy / "reports" / "r.md").write_text("old engagement")
        root = tmp_path / "suijin_agent"

        assert ws.ensure_workspace_layout(base_dir=base, workspace_dir=root) is True

        assert not legacy.exists()  # renamed away
        assert (root / "reports" / "r.md").read_text() == "old engagement"
        assert (base / "suijin_agent").is_symlink()

    def test_legacy_root_merged_when_both_exist(self, tmp_path):
        base = tmp_path / "suijin"
        base.mkdir()
        root = tmp_path / "suijin_agent"
        (root / "sessions").mkdir(parents=True)
        (root / "sessions" / "new.json").write_text("{}")
        legacy = tmp_path / "medusa_agent"
        (legacy / "reports").mkdir(parents=True)
        (legacy / "reports" / "old.md").write_text("x")

        ws.ensure_workspace_layout(base_dir=base, workspace_dir=root)

        assert not legacy.exists()
        assert (root / "sessions" / "new.json").exists()  # kept
        assert (root / "reports" / "old.md").exists()  # merged in

    def test_stale_legacy_inner_symlink_removed(self, tmp_path):
        base = tmp_path / "suijin"
        base.mkdir()
        root = tmp_path / "suijin_agent"
        root.mkdir()
        stale = base / "medusa_agent"
        stale.symlink_to("../medusa_agent")  # dangling post-rename link

        assert ws.ensure_workspace_layout(base_dir=base, workspace_dir=root) is True

        assert not stale.exists()  # cleaned
        assert (base / "suijin_agent").is_symlink()  # correct one created

    def test_legacy_inner_real_dir_merged(self, tmp_path):
        base = tmp_path / "suijin"
        legacy_inner = base / "medusa_agent"
        (legacy_inner / "outputs").mkdir(parents=True)
        (legacy_inner / "outputs" / "job.log").write_text("log")
        root = tmp_path / "suijin_agent"

        ws.ensure_workspace_layout(base_dir=base, workspace_dir=root)

        assert not legacy_inner.exists()
        assert (root / "outputs" / "job.log").read_text() == "log"


class TestOutputsConsolidation:
    def test_artifacts_nest_under_outputs(self, tmp_path, monkeypatch):
        """v4.2 contract: every artifact category lives under outputs/ —
        one parent for everything an engagement produces."""
        import suijin.modules.platform.lib.workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        for name in ws.ARTIFACT_DIRS:
            d = ws.artifact_dir(name)
            assert d.parent == tmp_path / "outputs", name

    def test_legacy_artifacts_migrate_once(self, tmp_path, monkeypatch):
        import suijin.modules.platform.lib.workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        (tmp_path / "reports").mkdir()
        (tmp_path / "reports" / "old.md").write_text("x")
        moved = ws.migrate_legacy_artifacts()
        assert "reports" in moved
        assert (tmp_path / "outputs" / "reports" / "old.md").read_text() == "x"
        assert not (tmp_path / "reports").exists()
        assert ws.migrate_legacy_artifacts() == []  # idempotent
