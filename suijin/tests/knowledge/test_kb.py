"""Tests for the knowledge base: compile, FTS search, status, CLI plumbing.

All tests are offline — tarball fixtures are built in tmp_path, never fetched.
"""

import io
import sqlite3
import tarfile

import pytest

from suijin.modules.knowledge.lib import kb as kbmod
from suijin.modules.knowledge.lib.kb import compile_kb, iter_docs, kb_status
from suijin.modules.tools.lib.intel import _fts_match_expr


def _make_tar(files: dict[str, str]) -> bytes:
    """Build an in-memory gzipped tarball: {member_name: content}."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch):
    """Zero out download retry backoff — keeps failure-path tests instant."""
    monkeypatch.setattr(kbmod, "RETRY_DELAYS", (0, 0))


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """One cached tarball per source, monkeypatched SOURCES — no network."""
    db = tmp_path / "kb.sqlite3"
    cache = tmp_path / "cache"
    monkeypatch.setattr(
        kbmod,
        "SOURCES",
        {
            "payloads": {"repo": "example/payloads", "patterns": ["*.md"]},
            "seclists": {"repo": "example/seclists", "patterns": ["*.txt"]},
        },
    )
    cache.mkdir()
    (cache / "payloads.tar.gz").write_bytes(
        _make_tar(
            {
                "payloads-abc123/README.md": "# Payloads Repo\n\nSQL injection cheatsheet content here.",
                "payloads-abc123/docs/sqli.md": "# SQLi\n\n' OR 1=1 -- union select payloads",
                "payloads-abc123/img.png": "binary-not-matched",
            }
        )
    )
    (cache / "seclists.tar.gz").write_bytes(
        _make_tar(
            {
                "SecLists-abc123/Passwords/common.txt": "password123\nletmein\n",
                "SecLists-abc123/Discovery/Common.txt": "admin\nbackup\n",
            }
        )
    )
    return {"db": db, "cache": cache}


class TestCompile:
    def test_compile_builds_fts_db(self, fake_env):
        summary = compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)
        assert summary["_total"] == 4
        assert summary["payloads"] == 2
        assert summary["seclists"] == 2
        assert summary["_fts5"] is True
        assert fake_env["db"].exists()

    def test_rebuild_is_idempotent(self, fake_env):
        compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)
        summary = compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)
        assert summary["_total"] == 4

    def test_unknown_source_rejected(self, fake_env):
        with pytest.raises(ValueError, match="Unknown KB source"):
            compile_kb(sources=["nope"], db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)

    def test_kb_status(self, fake_env):
        assert kb_status(fake_env["db"]) is None
        compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)
        st = kb_status(fake_env["db"])
        assert st["docs"] == 4
        assert st["sources"] == 2
        assert st["built_at"]

    def test_content_cap_applied(self, fake_env, monkeypatch):
        monkeypatch.setattr(kbmod, "MAX_CONTENT_BYTES", 100)
        compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)
        conn = sqlite3.connect(fake_env["db"])
        row = conn.execute("SELECT content FROM kb_files WHERE path LIKE '%sqli.md'").fetchone()
        conn.close()
        assert len(row[0]) <= 100 + len("\n... [truncated]")

    def test_oversize_files_skipped(self, fake_env, monkeypatch):
        monkeypatch.setattr(kbmod, "MAX_FILE_BYTES", 5)
        docs = list(iter_docs(fake_env["cache"] / "payloads.tar.gz", "payloads", ["*.md"]))
        assert docs == []  # every fixture file exceeds the cap

    def test_all_oversize_is_build_failure(self, fake_env, monkeypatch):
        # 0 docs across ALL sources is a failed build — no empty DB ships
        monkeypatch.setattr(kbmod, "MAX_FILE_BYTES", 5)
        with pytest.raises(RuntimeError, match="all sources failed"):
            compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)
        assert not fake_env["db"].exists()


class TestIterDocs:
    def test_pattern_filter_and_title(self, fake_env):
        docs = list(iter_docs(fake_env["cache"] / "payloads.tar.gz", "payloads", ["*.md"]))
        paths = {p for _, p, _, _ in docs}
        assert paths == {"README.md", "docs/sqli.md"}
        titles = {t for _, _, t, _ in docs}
        assert "Payloads Repo" in titles  # first # heading becomes the title


class TestSearchKbTool:
    def test_disabled_when_not_built(self, monkeypatch):
        from suijin.modules.tools.lib import intel

        monkeypatch.setattr(intel, "DB_PATH", __import__("pathlib").Path("/nonexistent/kb.sqlite3"))
        result = intel.search_kb("sqli")
        assert "disabled" in result.lower()
        assert "suijin pull kb" in result

    def test_fts_search_returns_source_and_snippet(self, fake_env, monkeypatch):
        from suijin.modules.tools.lib import intel

        compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)
        monkeypatch.setattr(intel, "DB_PATH", fake_env["db"])
        result = intel.search_kb("union select")
        assert "[payloads]" in result
        assert "sqli.md" in result
        assert "union select" in result.lower() or "…" in result

    def test_no_match_message(self, fake_env, monkeypatch):
        from suijin.modules.tools.lib import intel

        compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)
        monkeypatch.setattr(intel, "DB_PATH", fake_env["db"])
        assert "No matching" in intel.search_kb("zzz nonexistent topic zzz")

    def test_like_fallback_when_fts_missing(self, fake_env, monkeypatch):
        from suijin.modules.tools.lib import intel

        compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)
        # Simulate a DB built without FTS5: drop the virtual table so the
        # FTS query raises and search falls back to LIKE scanning.
        conn = sqlite3.connect(fake_env["db"])
        conn.execute("DROP TABLE kb_fts")
        conn.commit()
        conn.close()
        monkeypatch.setattr(intel, "DB_PATH", fake_env["db"])
        result = intel.search_kb("letmein")
        assert "seclists" in result


class TestFtsQuerySanitizer:
    def test_terms_quoted(self):
        assert _fts_match_expr("union select") == '"union" "select"'

    def test_quotes_stripped(self):
        # embedded quotes are removed; remaining text becomes quoted terms
        assert _fts_match_expr('sql"injec') == '"sql" "injec"'

    def test_empty(self):
        assert _fts_match_expr('""') == '""'


class TestFetchSource:
    def test_unknown_source(self):
        with pytest.raises(ValueError, match="Unknown KB source"):
            kbmod.fetch_source("nope")

    def test_uses_cache_without_network(self, fake_env):
        # cached tarball exists -> returns immediately with a null session
        path = kbmod.fetch_source("payloads", cache_dir=fake_env["cache"], session=object(), log=lambda *_: None)
        assert path.name == "payloads.tar.gz"


class _FakeResp:
    def __init__(self, status_code=200, chunks=(b"tarball-bytes",)):
        self.status_code = status_code
        self._chunks = chunks

    def iter_content(self, chunk_size=None):
        return iter(self._chunks)


class TestFetchRetries:
    """Transient failures retry with backoff; .part never survives a failure."""

    def test_succeeds_on_third_attempt(self, fake_env):
        calls = {"n": 0}

        class Flaky:
            def get(self, *_a, **_k):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise ConnectionError("flaky wifi")
                return _FakeResp()

        path = kbmod.fetch_source(
            "payloads", cache_dir=fake_env["cache"], session=Flaky(), log=lambda *_: None, force=True
        )
        assert path.exists()
        assert calls["n"] == 3

    def test_404_does_not_retry_same_ref(self, fake_env):
        calls = {"n": 0}

        class NotFound:
            def get(self, *_a, **_k):
                calls["n"] += 1
                return _FakeResp(status_code=404)

        with pytest.raises(RuntimeError, match="failed to download"):
            kbmod.fetch_source(
                "payloads", cache_dir=fake_env["cache"], session=NotFound(), log=lambda *_: None, force=True
            )
        # one 404 per ref (HEAD, master, main) — no retries on a dead ref
        assert calls["n"] == 3

    def test_part_cleanup_on_failure(self, fake_env):
        # a stale partial from an earlier aborted run must not survive
        (fake_env["cache"] / "payloads.tar.gz").unlink()  # force a fresh download
        part = fake_env["cache"] / "payloads.tar.part"
        part.write_bytes(b"half a tarball")

        class Dead:
            def get(self, *_a, **_k):
                raise ConnectionError("no network")

        logs = []
        with pytest.raises(RuntimeError, match="failed to download"):
            kbmod.fetch_source("payloads", cache_dir=fake_env["cache"], session=Dead(), log=logs.append)
        assert not part.exists()
        assert any("stale partial" in line for line in logs)
        assert not (fake_env["cache"] / "payloads.tar.gz").exists()

    def test_mid_download_exception_cleans_part(self, fake_env):
        class Exploding:
            def iter_content(self, chunk_size=None):
                yield b"some bytes"
                raise ConnectionError("wifi died mid-stream")

        class Session:
            def get(self, *_a, **_k):
                return Exploding()

        with pytest.raises(RuntimeError, match="failed to download"):
            kbmod.fetch_source(
                "payloads", cache_dir=fake_env["cache"], session=Session(), log=lambda *_: None, force=True
            )
        assert not (fake_env["cache"] / "payloads.tar.part").exists()


class TestZeroDocSources:
    """A download that indexes 0 files is a FAILURE, not a silent success."""

    def test_zero_docs_recorded_as_failed_with_hint(self, fake_env, monkeypatch):
        monkeypatch.setattr(
            kbmod,
            "SOURCES",
            {
                "payloads": {"repo": "example/payloads", "patterns": ["*.md"]},
                "images": {"repo": "example/images", "patterns": ["*.md"]},
            },
        )
        (fake_env["cache"] / "images.tar.gz").write_bytes(
            _make_tar(
                {
                    "images-abc123/pic.png": "binary",  # matches no pattern
                }
            )
        )
        summary = compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)
        assert summary["payloads"] == 2
        assert "images" not in {k for k in summary if not k.startswith("_")}
        assert "0 files matched" in summary["_failed"]["images"]

        st = kb_status(fake_env["db"])
        assert st["docs"] == 2
        assert st["sources"] == 1  # only what actually indexed
        assert st["per_source"] == {"payloads": 2}
        assert "images" in st["failed"]


class TestPathPatterns:
    """Path-scoped patterns (GTFOBins layout: extensionless md under a subtree)."""

    @pytest.fixture
    def gtfo_env(self, tmp_path, monkeypatch):
        db = tmp_path / "kb.sqlite3"
        cache = tmp_path / "cache"
        monkeypatch.setattr(
            kbmod,
            "SOURCES",
            {
                "gtfobins": {
                    "repo": "GTFOBins/GTFOBins.github.io",
                    "patterns": ["_gtfobins/*"],
                    "resolve_aliases": True,
                },
            },
        )
        cache.mkdir()
        (cache / "gtfobins.tar.gz").write_bytes(
            _make_tar(
                {
                    "GTFOBins.github.io-abc123/_gtfobins/awk": "---\nalias: mawk\n...\n",
                    "GTFOBins.github.io-abc123/_gtfobins/mawk": "functions:\n  shell:\n    - code: awk 'BEGIN {system(\"/bin/sh\")}'\n  sudo:\n    - code: sudo awk 'BEGIN {system(\"/bin/sh\")}'",
                    "GTFOBins.github.io-abc123/_gtfobins/R": "functions:\n  shell:\n    - code: R -e 'system(\"/bin/sh\")'",
                    "GTFOBins.github.io-abc123/README.md": "# GTFOBins — not under _gtfobins/",
                    "GTFOBins.github.io-abc123/_gtfobins/sub/deep.txt": "nested file",
                }
            )
        )
        return {"db": db, "cache": cache}

    def test_subtree_pattern_scopes_correctly(self, gtfo_env):
        docs = list(iter_docs(gtfo_env["cache"] / "gtfobins.tar.gz", "gtfobins", ["_gtfobins/*"]))
        paths = {p for _, p, _, _ in docs}
        # top-level _gtfobins/ entries only — README excluded, nested subdir
        # still matches (fnmatch * crosses /)
        assert paths == {"_gtfobins/awk", "_gtfobins/mawk", "_gtfobins/R", "_gtfobins/sub/deep.txt"}

    def test_alias_stubs_resolved_to_target_content(self, gtfo_env, monkeypatch):
        from suijin.modules.tools.lib import intel

        compile_kb(db_path=gtfo_env["db"], cache_dir=gtfo_env["cache"], log=lambda *_: None)
        monkeypatch.setattr(intel, "DB_PATH", gtfo_env["db"])
        result = intel.search_kb("source:gtfobins awk sudo")
        assert "_gtfobins/awk" in result  # the stub's path
        assert "alias of mawk" in result  # resolved to mawk's functions
        assert "sudo awk" in result

    def test_gtfobins_source_uses_live_repo(self):
        assert kbmod.SOURCES["gtfobins"]["repo"] == "GTFOBins/GTFOBins.github.io"
        assert "_gtfobins/*" in kbmod.SOURCES["gtfobins"]["patterns"]

    def test_compile_indexes_extensionless_docs(self, gtfo_env):
        summary = compile_kb(db_path=gtfo_env["db"], cache_dir=gtfo_env["cache"], log=lambda *_: None)
        assert summary["gtfobins"] == 4
        st = kb_status(gtfo_env["db"])
        assert st["per_source"] == {"gtfobins": 4}
        assert st["size_bytes"] > 0
        assert st["fts5"] is True

    def test_seclists_has_size_note(self):
        assert "note" in kbmod.SOURCES["seclists"]


class TestSearchKbSourceFilterAndLimit:
    def test_source_filter_scopes_results(self, fake_env, monkeypatch):
        from suijin.modules.tools.lib import intel

        compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)
        monkeypatch.setattr(intel, "DB_PATH", fake_env["db"])
        result = intel.search_kb("source:seclists letmein")
        assert "[seclists]" in result
        assert "[payloads]" not in result

    def test_source_filter_unknown_reports_available(self, fake_env, monkeypatch):
        from suijin.modules.tools.lib import intel

        compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)
        monkeypatch.setattr(intel, "DB_PATH", fake_env["db"])
        result = intel.search_kb("source:nope query")
        assert "no docs in this build" in result
        assert "payloads" in result and "seclists" in result

    def test_limit_clamps_results(self, fake_env, monkeypatch):
        from suijin.modules.tools.lib import intel

        compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)
        monkeypatch.setattr(intel, "DB_PATH", fake_env["db"])
        result = intel.search_kb("union select", limit=1)
        assert result.count("--- [") == 1
        # limit is clamped to 1..20, garbage falls back to 5
        assert intel.search_kb("union select", limit=99).count("--- [") <= 5
        assert intel.search_kb("union select", limit="garbage").count("--- [") <= 5

    def test_source_filter_with_like_fallback(self, fake_env, monkeypatch):
        from suijin.modules.tools.lib import intel

        compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)
        conn = sqlite3.connect(fake_env["db"])
        conn.execute("DROP TABLE kb_fts")
        conn.commit()
        conn.close()
        monkeypatch.setattr(intel, "DB_PATH", fake_env["db"])
        result = intel.search_kb("source:seclists letmein")
        assert "[seclists]" in result
        assert "[payloads]" not in result


class TestRepoAnchoredPaths:
    """KB artifacts must ALWAYS live inside the suijin-security repo folder —
    never CWD-dependent, never scattered into $HOME or /tmp."""

    def test_paths_anchored_to_workspace_caches(self, monkeypatch):
        # v4.1: runtime data lives in the agent workspace (caches/), not
        # the package — a built KB survives reinstalls and, in Docker,
        # container recreation (workspace is the volume)
        from suijin.modules.platform.lib import workspace as _ws

        # v5.3: force repo-local AND recomputed kb paths (DB_PATH/CACHE_DIR
        # resolve at kb import; the durable workspace may have won there)
        monkeypatch.setattr(_ws, "WORKSPACE_DIR", _ws.PROJECT_DIR / "suijin_agent")
        monkeypatch.setattr(kbmod, "DB_PATH", _ws.WORKSPACE_DIR / "caches" / "kb.sqlite3")
        monkeypatch.setattr(kbmod, "CACHE_DIR", _ws.WORKSPACE_DIR / "caches" / "kb_cache")
        caches = _ws.WORKSPACE_DIR / "caches"
        assert caches / "kb.sqlite3" == kbmod.DB_PATH
        assert caches / "kb_cache" == kbmod.CACHE_DIR
        assert kbmod.DB_PATH.is_absolute()
        assert kbmod.CACHE_DIR.is_absolute()

    def test_paths_stable_regardless_of_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

        caches = WORKSPACE_DIR / "caches"
        assert caches / "kb.sqlite3" == kbmod.DB_PATH
        assert caches / "kb_cache" == kbmod.CACHE_DIR


class TestPartialFailureResilience:
    """One dead source must not kill the whole pull."""

    def test_failed_source_skipped_and_reported(self, fake_env, monkeypatch):
        # Second source's cached tarball missing and no network -> fails,
        # but payloads still compiles into the DB.
        (fake_env["cache"] / "seclists.tar.gz").unlink()

        class _NoNetwork:
            def get(self, *_a, **_k):
                raise ConnectionError("no network")

        logs = []
        summary = compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], session=_NoNetwork(), log=logs.append)
        assert summary["payloads"] == 2
        assert summary["_total"] == 2
        assert "seclists" in summary["_failed"]
        assert any("FAILED" in line for line in logs)
        # DB exists and is searchable despite the partial failure
        assert fake_env["db"].exists()
        assert kb_status(fake_env["db"])["docs"] == 2

    def test_all_sources_failed_raises(self, fake_env):
        (fake_env["cache"] / "payloads.tar.gz").unlink()
        (fake_env["cache"] / "seclists.tar.gz").unlink()

        class _NoNetwork:
            def get(self, *_a, **_k):
                raise ConnectionError("no network")

        with pytest.raises(RuntimeError, match="all sources failed"):
            compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], session=_NoNetwork(), log=lambda *_: None)
        # No half-built DB left behind
        assert not fake_env["db"].exists()


class TestCatalogFeatureGating:
    """`suijin pull kb` ENABLES knowledge base features; without it they are
    advertised as disabled — the agent must never waste turns calling a
    disabled tool."""

    def test_catalog_lists_disabled_when_no_db(self, monkeypatch):
        import suijin.modules.knowledge.lib.kb as kb_mod
        from suijin.modules.knowledge.lib.kb import DB_PATH as real_db
        from suijin.modules.tools.lib import dispatch

        monkeypatch.setattr(kb_mod, "DB_PATH", real_db.parent / "definitely_missing_kb.sqlite3")
        catalog = dispatch.get_tool_catalog()
        assert "DISABLED" in catalog
        assert "suijin pull kb" in catalog

    def test_catalog_advertises_search_kb_when_built(self, fake_env, monkeypatch):
        import suijin.modules.knowledge.lib.kb as kb_mod
        from suijin.modules.tools.lib import dispatch

        compile_kb(db_path=fake_env["db"], cache_dir=fake_env["cache"], log=lambda *_: None)
        monkeypatch.setattr(kb_mod, "DB_PATH", fake_env["db"])
        catalog = dispatch.get_tool_catalog()
        assert "**search_kb**" in catalog
        assert "docs:" in catalog
        assert "DISABLED" not in catalog


class TestWorkspaceIntegrity:
    """The agent workspace (suijin_agent/) is sacred — KB artifacts must
    never leak into it, and its anchoring must not change."""

    def test_kb_lives_in_workspace_caches_not_scattered(self, monkeypatch):
        """v4.1: runtime data (kb db + caches) lives INSIDE the workspace
        under caches/ — one volume to rule them all — but never scattered
        at the workspace root."""
        from suijin.modules.platform.lib import workspace as _ws

        # v5.3: WORKSPACE_DIR may resolve durable (~/.suijin/workspace) on
        # an installed layout — force repo-local and align kb's paths
        monkeypatch.setattr(_ws, "WORKSPACE_DIR", _ws.PROJECT_DIR / "suijin_agent")
        monkeypatch.setattr(kbmod, "DB_PATH", _ws.WORKSPACE_DIR / "caches" / "kb.sqlite3")
        monkeypatch.setattr(kbmod, "CACHE_DIR", _ws.WORKSPACE_DIR / "caches" / "kb_cache")
        WORKSPACE_DIR, PROJECT_DIR = _ws.WORKSPACE_DIR, _ws.PROJECT_DIR

        assert WORKSPACE_DIR == PROJECT_DIR / "suijin_agent"
        caches = WORKSPACE_DIR / "caches"
        assert kbmod.DB_PATH.parent == caches
        assert kbmod.CACHE_DIR.parent == caches
        assert kbmod.DB_PATH != WORKSPACE_DIR / "kb.sqlite3"  # not at root
