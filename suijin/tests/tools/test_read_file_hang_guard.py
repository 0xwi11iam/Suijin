"""read_file hang guard — FIFOs/devices refuse instantly, huge files
read head+tail, regular files unchanged.

The field bug: read_text() on a named pipe blocked forever; the operator
had to interject to break the engagement out of the loop.
"""

import os
import time

import pytest


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    import suijin.modules.platform.lib.workspace as ws

    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    ws._reset_engagement()  # hermetic: no engagement pinned from another test
    # jail_path passes through anything under the ENGAGEMENT home — the
    # workspace root alone is not a pass-through root, so test files live
    # in home/ like real agent files.
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr("suijin.server.confinement._engagement_root", lambda root=None: tmp_path)
    return ws


def _read(path):
    from suijin.server.tools.lib.http_tools import read_file

    return read_file(str(path))


class TestHangGuard:
    def test_fifo_refused_instantly(self, tmp_path):
        """A named pipe must NOT block — this test would time out (2s)
        under the old read_text() behavior."""
        fifo = tmp_path / "home" / "stream.pipe"
        os.mkfifo(fifo)
        start = time.time()
        out = _read(fifo)
        elapsed = time.time() - start
        assert "block forever" in out and "FIFO" in out
        assert elapsed < 1.0  # refused, not hung

    def test_directory_refused_with_hint(self, tmp_path):
        out = _read(tmp_path / "home")
        assert "directory" in out and "block forever" in out

    def test_huge_file_head_and_tail(self, tmp_path):
        big = tmp_path / "home" / "big.log"
        big.write_text("HEAD-MARKER\n" + "x" * 3_000_000 + "\nTAIL-MARKER\n")
        out = _read(big)
        assert "HEAD-MARKER" in out and "TAIL-MARKER" in out
        assert "bytes omitted mid-file" in out
        assert len(out) < 250_000  # never the raw 3MB

    def test_normal_file_unchanged(self, tmp_path):
        f = tmp_path / "home" / "note.txt"
        f.write_text("ordinary content")
        out = _read(f)
        assert "ordinary content" in out and "Error" not in out

    def test_missing_file_still_clean(self, tmp_path):
        out = _read(tmp_path / "home" / "nope.txt")
        assert out.startswith("Error: File not found")
