"""Tests for KB v2 (read_doc, kb_diff, fuzzy suggest, kb_read tool) and the
CVE KEV mirror + offline search_cve fallback + wordlist tools."""

import io
import tarfile

import pytest

from suijin.modules.knowledge.lib import cve_mirror, kb_tools
from suijin.modules.knowledge.lib import kb as kbmod
from suijin.modules.knowledge.lib.kb import compile_kb, kb_diff, read_doc
from suijin.modules.tools.lib.wordlist_mutator import cewl_words, extract_words, mutate_wordlist


def _make_tar(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


LONG = "A" * 300_000  # far beyond MAX_CONTENT_BYTES


@pytest.fixture
def kb_env(tmp_path, monkeypatch):
    db = tmp_path / "kb.sqlite3"
    cache = tmp_path / "cache"
    ws = tmp_path / "suijin_agent"
    ws.mkdir()
    monkeypatch.setattr(
        kbmod,
        "SOURCES",
        {
            "payloads": {"repo": "example/payloads", "patterns": ["*.md"]},
            "gtfobins": {"repo": "GTFOBins/GTFOBins.github.io", "patterns": ["_gtfobins/*"], "resolve_aliases": True},
        },
    )
    cache.mkdir()
    (cache / "payloads.tar.gz").write_bytes(
        _make_tar(
            {
                "payloads-x/sqli.md": "# SQLi\n\nshort doc",
                "payloads-x/big.md": f"# Big\n\n{LONG}",
            }
        )
    )
    (cache / "gtfobins.tar.gz").write_bytes(
        _make_tar(
            {
                "gtfo-x/_gtfobins/awk": "functions:\n  shell:\n    - code: mawk ...",
                "gtfo-x/_gtfobins/find": "functions:\n  file-read:\n    - code: find ...",
            }
        )
    )
    summary = compile_kb(db_path=db, cache_dir=cache, log=lambda *_: None)
    monkeypatch.setattr(kbmod, "DB_PATH", db)
    monkeypatch.setattr(kbmod, "CACHE_DIR", cache)  # used by read_doc defaults
    monkeypatch.setattr(kb_tools, "DB_PATH", db)
    monkeypatch.setattr(kb_tools, "CACHE_DIR", cache)
    import suijin.modules.platform.lib.workspace as pws

    monkeypatch.setattr(pws, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(pws, "resolve_workspace_path", lambda p: (ws / p).resolve())
    return {"db": db, "cache": cache, "ws": ws, "summary": summary}


class TestKbRead:
    def test_read_full_doc_untruncated(self, kb_env):
        source, path, content = read_doc("big.md", cache_dir=kb_env["cache"])
        assert source == "payloads"
        assert path == "big.md"
        assert len(content) > 290_000  # the index copy would be 256k-capped

    def test_substring_match_infers_source(self, kb_env):
        source, path, _ = read_doc("_gtfobins/awk", cache_dir=kb_env["cache"])
        assert (source, path) == ("gtfobins", "_gtfobins/awk")

    def test_missing_and_ambiguous(self, kb_env):
        with pytest.raises(FileNotFoundError):
            read_doc("zzz-nope.md", cache_dir=kb_env["cache"])
        # a needle matching docs in TWO different sources is ambiguous
        (kb_env["cache"] / "gtfobins.tar.gz").write_bytes(_make_tar({"gtfo-x/_gtfobins/shared_name": "x"}))
        (kb_env["cache"] / "payloads.tar.gz").write_bytes(_make_tar({"payloads-x/shared_name.md": "y"}))
        with pytest.raises(ValueError, match="Ambiguous"):
            read_doc("shared_name", cache_dir=kb_env["cache"])

    def test_kb_read_agent_tool(self, kb_env, monkeypatch):
        from suijin.modules.tools.lib import dispatch

        out = dispatch.route_tool("kb_read", {"path": "_gtfobins/awk"}, {})
        assert "[gtfobins]" in out
        assert "mawk" in out
        err = dispatch.route_tool("kb_read", {"path": "zzz"}, {})
        assert "Error" in err


class TestKbDiff:
    def test_fresh_build_all_ok(self, kb_env):
        d = kb_diff(kb_env["db"], kb_env["cache"])
        assert d["built"] is True
        assert all(v["action"] == "ok" for v in d["sources"].values() if v["indexed_docs"] is not None)

    def test_newer_cache_flags_rebuild(self, kb_env):
        import os

        tar = kb_env["cache"] / "payloads.tar.gz"
        future = tar.stat().st_mtime + 3600
        os.utime(tar, (future, future))
        d = kb_diff(kb_env["db"], kb_env["cache"])
        assert d["sources"]["payloads"]["cache_newer_than_build"] is True
        assert d["sources"]["payloads"]["action"] == "rebuild"

    def test_unindexed_cache(self, kb_env):
        (kb_env["cache"] / "seclists.tar.gz").write_bytes(_make_tar({"s/x.txt": "w"}))
        monkeypatch_srcs = {"seclists": {"repo": "example/s", "patterns": ["*.txt"]}}
        orig = dict(kbmod.SOURCES)
        kbmod.SOURCES.update(monkeypatch_srcs)
        try:
            d = kb_diff(kb_env["db"], kb_env["cache"])
        finally:
            kbmod.SOURCES.clear()
            kbmod.SOURCES.update(orig)
        assert d["sources"]["seclists"]["action"] == "pull"


class TestFuzzyGtfo:
    def test_fuzzy_bin_match(self, kb_env):
        out = kb_tools.suggest_exploit("finnd")  # close to 'find'
        assert "fuzzy: finnd ~ find" in out

    def test_exact_still_works(self, kb_env):
        out = kb_tools.suggest_exploit("awk")
        assert "_gtfobins/awk" in out and "fuzzy" not in out


# ── CVE KEV mirror ─────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class TestKevMirror:
    def test_pull_writes_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cve_mirror, "CVE_CACHE_DIR", tmp_path)
        monkeypatch.setattr(cve_mirror, "KEV_PATH", tmp_path / "kev.json")
        catalog = {
            "count": 2,
            "vulnerabilities": [
                {
                    "cveID": "CVE-2021-44228",
                    "product": "Log4j",
                    "vendorProject": "Apache",
                    "vulnerabilityName": "Log4Shell RCE",
                    "description": "remote code execution",
                },
                {
                    "cveID": "CVE-2023-1234",
                    "product": "OpenSSL",
                    "vendorProject": "OpenSSL",
                    "vulnerabilityName": "buffer overflow",
                    "description": "heap overflow",
                },
            ],
        }
        pulled = cve_mirror.pull_kev(
            session=type("S", (), {"get": lambda self, url, timeout: _FakeResp(catalog)})(), log=lambda *_: None
        )
        assert pulled["count"] == 2
        assert cve_mirror.kev_status()["count"] == 2

    def test_search_kev_offline(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cve_mirror, "CVE_CACHE_DIR", tmp_path)
        monkeypatch.setattr(cve_mirror, "KEV_PATH", tmp_path / "kev.json")
        cve_mirror.pull_kev(
            session=type(
                "S",
                (),
                {
                    "get": lambda self, url, timeout: _FakeResp(
                        {
                            "count": 1,
                            "vulnerabilities": [
                                {
                                    "cveID": "CVE-2021-44228",
                                    "product": "Log4j",
                                    "vendorProject": "Apache",
                                    "vulnerabilityName": "Log4Shell",
                                }
                            ],
                        }
                    )
                },
            )(),
            log=lambda *_: None,
        )
        hits = cve_mirror.search_kev("apache log4j")
        assert hits and hits[0]["cveID"] == "CVE-2021-44228"
        assert cve_mirror.search_kev("totally unrelated thing") == []

    def test_search_cve_falls_back_to_kev(self, tmp_path, monkeypatch):
        from suijin.modules.tools.lib import intel

        monkeypatch.setattr(cve_mirror, "CVE_CACHE_DIR", tmp_path)
        monkeypatch.setattr(cve_mirror, "KEV_PATH", tmp_path / "kev.json")
        cve_mirror.pull_kev(
            session=type(
                "S",
                (),
                {
                    "get": lambda self, url, timeout: _FakeResp(
                        {
                            "count": 1,
                            "vulnerabilities": [
                                {
                                    "cveID": "CVE-2021-44228",
                                    "product": "Log4j",
                                    "vendorProject": "Apache",
                                    "vulnerabilityName": "Log4Shell",
                                    "dueDate": "2021-12-24",
                                }
                            ],
                        }
                    )
                },
            )(),
            log=lambda *_: None,
        )

        import requests as real_requests

        class _Dead:
            def get(self, *_a, **_k):
                raise real_requests.exceptions.ConnectionError("no network")

        monkeypatch.setattr(intel.requests, "get", _Dead().get)
        out = intel.search_cve("log4j", {}, version=None, limit=3)
        assert "[KEV offline]" in out
        assert "CVE-2021-44228" in out


# ── Wordlist mutation + cewl ────────────────────────────────────────────


class TestMutateWordlist:
    def test_expansion(self, tmp_path, monkeypatch):
        from suijin.modules.tools.lib import wordlist_mutator as wm

        monkeypatch.setattr(wm, "WORKSPACE_DIR", tmp_path)
        monkeypatch.setattr(wm, "resolve_workspace_path", lambda p: (tmp_path / p).resolve())
        out = mutate_wordlist(["admin", "Admin@Test.example"])
        assert "word(s)" in out
        words = (tmp_path / "wordlists" / "mutated.txt").read_text().split()
        assert "admin" in words
        assert "admin123" in words
        assert "4dm1n" in words  # leet: a->4, i->1
        assert "admin2024" in words  # years
        assert "Admin" in words  # email local-part kept

    def test_requires_seeds(self):
        assert "Error" in mutate_wordlist([])


class TestCewl:
    def test_extract_words(self):
        words = extract_words(
            "<html><script>ignore()</script><body>Welcome to Example Corp — login here, admin!</body></html>"
        )
        assert "Welcome" in words and "Example" in words and "admin" in words
        assert "ignore" not in words  # script content stripped
        assert all(len(w) >= 3 for w in words)

    def test_cewl_fetches_and_writes(self, tmp_path, monkeypatch):
        from suijin.modules.tools.lib import wordlist_mutator as wm

        monkeypatch.setattr(wm, "WORKSPACE_DIR", tmp_path)
        monkeypatch.setattr(wm, "resolve_workspace_path", lambda p: (tmp_path / p).resolve())

        class _Resp:
            text = "<html><body>Example mathematics platform teachers students</body></html>"

        class _Sess:
            def get(self, url, timeout):
                return _Resp()

        out = cewl_words("https://example.com", session=_Sess())
        assert "harvested" in out
        words = list((tmp_path / "wordlists").glob("cewl_*"))
        assert words and "teachers" in words[0].read_text()

    def test_cewl_error(self):
        assert "Error" in cewl_words("")
