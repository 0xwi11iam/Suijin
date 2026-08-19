"""F45 — wordlist hub."""

from unittest import mock

from suijin.modules.knowledge.lib import wordlist_hub


class _FakeResp:
    status_code = 200

    def __init__(self, chunks):
        self._chunks = chunks

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        yield from self._chunks


class TestHub:
    def test_catalog_lists_curated(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        out = wordlist_hub.catalog()
        assert "common" in out and "dirb-common" in out and "[-]" in out

    def test_fetch_verifies_sha(self, tmp_path, monkeypatch):
        import hashlib

        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        payload = b"user1\nuser2\n"
        good_sha = hashlib.sha256(payload).hexdigest()[:16]
        with mock.patch.object(wordlist_hub.requests, "get", return_value=_FakeResp([payload])):
            out = wordlist_hub.fetch(url="https://x/l.txt", sha_first16=good_sha, name="x")
        assert "fetched" in out
        # mismatch rejected, nothing lands
        with mock.patch.object(wordlist_hub.requests, "get", return_value=_FakeResp([payload])):
            out2 = wordlist_hub.fetch(url="https://x/l.txt", sha_first16="0000000000000000", name="y")
        assert "CHECKSUM MISMATCH" in out2
        assert not (tmp_path / "outputs" / "wordlists" / "y.txt").exists()

    def test_fetch_unknown_name(self):
        assert "unknown name" in wordlist_hub.fetch("nope")

    def test_size_cap(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        big = [b"x" * 65536] * ((wordlist_hub.MAX_BYTES // 65536) + 2)
        with mock.patch.object(wordlist_hub.requests, "get", return_value=_FakeResp(big)):
            out = wordlist_hub.fetch(url="https://x/big.txt", sha_first16="", name="z")
        assert "exceeded" in out
