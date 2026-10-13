"""Workspace layout tests — the v7.2 CLEAN LAYOUT.

The contract (operator ruling 2026-10-12): ~/.suijin holds config/
(WORKSPACE_DIR — profiles/skills/packs + operator settings) and
engagements/ (one folder per run) plus system logs/ and caches/.
Nothing else. The repo-level suijin_agent symlink is RETIRED —
ensure_workspace_layout removes stale ones and never creates them.
"""

from suijin.modules.platform.lib import workspace as ws


def _make(tmp_path):
    base = tmp_path / "suijin"
    base.mkdir()
    root = tmp_path / "suijin_agent"
    root.mkdir()
    return base, root


class TestEnsureWorkspaceLayout:
    def test_inner_real_dir_merged_no_symlink_created(self, tmp_path):
        """A legacy real inner dir merges UP into the workspace; the
        retired suijin_agent path is removed — a symlink is NEVER
        created (v7.2)."""
        base, root = _make(tmp_path)
        inner = base / "suijin_agent"
        (inner / "reports").mkdir(parents=True)
        (inner / "reports" / "r.md").write_text("report")

        assert ws.ensure_workspace_layout(base_dir=base, workspace_dir=root) is True

        assert (root / "reports" / "r.md").read_text() == "report"
        assert not inner.exists()
        assert not inner.is_symlink()

    def test_idempotent_when_clean(self, tmp_path):
        base, root = _make(tmp_path)
        assert ws.ensure_workspace_layout(base_dir=base, workspace_dir=root) is False
        assert ws.ensure_workspace_layout(base_dir=base, workspace_dir=root) is False
        assert not (base / "suijin_agent").exists()

    def test_stale_inner_symlink_removed(self, tmp_path):
        base, root = _make(tmp_path)
        (base / "suijin_agent").symlink_to("/definitely/not/anywhere")
        assert ws.ensure_workspace_layout(base_dir=base, workspace_dir=root) is True
        assert not (base / "suijin_agent").exists()

    def test_nested_dir_collision_merges_recursively(self, tmp_path):
        base, root = _make(tmp_path)
        inner = base / "suijin_agent"
        (inner / "toollogs").mkdir(parents=True)
        (inner / "toollogs" / "job.log").write_text("log")
        (root / "toollogs").mkdir()
        (root / "toollogs" / "keep.txt").write_text("keep")

        ws.ensure_workspace_layout(base_dir=base, workspace_dir=root)

        assert (root / "toollogs" / "job.log").read_text() == "log"
        assert (root / "toollogs" / "keep.txt").read_text() == "keep"
        assert not inner.exists()


class TestCanonicalLayout:
    def test_resolution_env_beats_all(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(tmp_path / "explicit"))
        assert ws._resolve_workspace() == tmp_path / "explicit"

    def test_resolution_installed_machine_uses_config(self, monkeypatch, tmp_path):
        """~/.suijin exists -> WORKSPACE_DIR is ~/.suijin/config, the
        clean layout — never the old workspace, never repo-local."""
        import pathlib

        scratch = tmp_path / "repo"
        (scratch / "suijin").mkdir(parents=True)
        monkeypatch.setattr(ws, "PROJECT_DIR", scratch)
        monkeypatch.delenv("SUIJIN_WORKSPACE", raising=False)
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: fake_home))
        assert ws._resolve_workspace() == fake_home / ".suijin" / "config"

    def test_resolution_bare_checkout_follows_repo_symlink(self, monkeypatch, tmp_path):
        """No ~/.suijin at all (bare source checkout): the repo-local
        suijin_agent symlink still resolves; a DANGLING one falls back
        to the config default (never mkdir a dead target)."""
        import pathlib

        scratch = tmp_path / "repo"
        (scratch / "suijin").mkdir(parents=True)
        target = tmp_path / "agent_root"
        target.mkdir()
        (scratch / "suijin_agent").symlink_to(target)
        monkeypatch.setattr(ws, "PROJECT_DIR", scratch)
        monkeypatch.delenv("SUIJIN_WORKSPACE", raising=False)
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: fake_home))
        assert ws._resolve_workspace() == target
        # dangling: remove and re-point at nothing
        (scratch / "suijin_agent").unlink()
        (scratch / "suijin_agent").symlink_to("/definitely/not/anywhere")
        assert ws._resolve_workspace() == fake_home / ".suijin" / "config"

    def test_clean_skeleton(self, tmp_path, monkeypatch):
        """ensure_global_layout creates exactly the v7.2 roots."""
        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path / "config")
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(tmp_path / "config"))  # guard the migrator
        ws.ensure_global_layout()
        assert (tmp_path / "config" / "profiles").is_dir()
        assert (tmp_path / "config" / "skills").is_dir()
        assert (tmp_path / "config" / "packs").is_dir()
        assert (tmp_path / "config" / "bugscope").is_dir()
        assert (tmp_path / "engagements").is_dir()
        assert (tmp_path / "logs").is_dir()
        assert (tmp_path / "caches").is_dir()
        assert not (tmp_path / "outputs").exists()
        assert not (tmp_path / "config" / "logs").exists()

    def test_tool_dirs_anchored_to_engagement_or_base(self, tmp_path, monkeypatch):
        from suijin.modules.tools.lib import audit_trail, report_exporter, session_replay

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path / "config")
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(tmp_path / "config"))
        eng = ws.set_engagement("anchor probe")
        for d in (audit_trail.AUDIT_DIR, report_exporter.REPORTS_DIR, session_replay.REPLAY_DIR):
            anchored = str(d).startswith(str(eng)) or str(d).startswith(str(tmp_path))
            assert anchored, d


class TestLegacyWorkspaceMigration:
    def test_survivors_fold_in_and_husk_parked(self, tmp_path, monkeypatch):
        """The retired ~/.suijin/workspace folds survivors into the
        clean layout and parks itself at workspace.retired-<stamp>."""
        import pathlib

        fake_home = tmp_path / "home"
        legacy = fake_home / ".suijin" / "workspace"
        (legacy / "profiles" / "ctf").mkdir(parents=True)
        (legacy / "profiles" / "ctf" / "SOUL.md").write_text("persona")
        (legacy / "engagements" / "20260101_000000_old.run").mkdir(parents=True)
        (legacy / "engagements" / "_default").mkdir(parents=True)
        (legacy / "outputs").mkdir()
        (legacy / "outputs" / "stray.txt").write_text("junk")
        monkeypatch.delenv("SUIJIN_WORKSPACE", raising=False)
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: fake_home))

        monkeypatch.setattr(ws, "WORKSPACE_DIR", fake_home / ".suijin" / "config")
        ws.ensure_global_layout()

        assert (fake_home / ".suijin" / "config" / "profiles" / "ctf" / "SOUL.md").read_text() == "persona"
        assert (fake_home / ".suijin" / "engagements" / "20260101_000000_old.run").is_dir()
        assert not (fake_home / ".suijin" / "engagements" / "_default").exists()
        parked = list((fake_home / ".suijin").glob("workspace.retired-*"))
        assert parked, "husk must be parked, not deleted"
        assert (parked[0] / "outputs" / "stray.txt").exists()  # junk survives in the husk

    def test_migration_runs_once(self, tmp_path, monkeypatch):
        import pathlib

        fake_home = tmp_path / "home"
        legacy = fake_home / ".suijin" / "workspace"
        legacy.mkdir(parents=True)
        monkeypatch.delenv("SUIJIN_WORKSPACE", raising=False)
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: fake_home))
        monkeypatch.setattr(ws, "WORKSPACE_DIR", fake_home / ".suijin" / "config")
        ws.ensure_global_layout()
        parked = list((fake_home / ".suijin").glob("workspace.retired-*"))
        assert parked
        ws.ensure_global_layout()
        assert len(list((fake_home / ".suijin").glob("workspace.retired-*"))) == 1


class TestHuskDiscipline:
    def test_idle_reads_conjure_nothing(self, tmp_path, monkeypatch):
        """engagement_dir() pre-engagement returns the _default path
        WITHOUT creating it — idle verbs must not litter."""
        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path / "config")
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(tmp_path / "config"))
        ws._reset_engagement()
        d = ws.engagement_dir()
        assert d.name == "_default"
        assert not d.exists()
        assert ws.audit_dir() == tmp_path / "config" / "audit"  # CLI-calls parking

    def test_husk_prune(self, tmp_path, monkeypatch):
        """An engagement with no events/audit/.sje and empty log/ is a
        husk from an aborted launch — the redteamer finally removes it."""
        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path / "config")
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(tmp_path / "config"))
        eng = ws.set_engagement("husk probe")
        has_life = (eng / "events.jsonl").exists() or (eng / "audit_trails").is_dir()
        has_save = any((eng / "state").glob("*.sje"))
        assert not has_life and not has_save and not any((eng / "log").glob("*"))


class TestOutputsConsolidation:
    def test_artifacts_nest_per_engagement(self, tmp_path, monkeypatch):
        """Engagement categories live INSIDE the engagement folder;
        global categories under config/."""
        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path / "config")
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(tmp_path / "config"))
        ws._reset_engagement()
        eng = ws.set_engagement("layout probe")
        for name in ws.ENGAGEMENT_SUBDIRS:
            d = ws.artifact_dir(name)
            assert d.parent == eng, name
        for name in ws.GLOBAL_DIRS:
            d = ws.artifact_dir(name)
            assert d.parent == tmp_path / "config", name

    def test_legacy_artifacts_migrate_once(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path / "config")
        assert ws.migrate_legacy_artifacts() == []


class TestRenameMigration:
    """Medusa -> Suijin rename: legacy medusa_agent data must carry over."""

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

    def test_legacy_inner_real_dir_merged(self, tmp_path):
        base = tmp_path / "suijin"
        legacy_inner = base / "medusa_agent"
        (legacy_inner / "toollogs").mkdir(parents=True)
        (legacy_inner / "toollogs" / "job.log").write_text("log")
        root = tmp_path / "suijin_agent"

        ws.ensure_workspace_layout(base_dir=base, workspace_dir=root)

        assert not legacy_inner.exists()
        assert (root / "toollogs" / "job.log").read_text() == "log"
