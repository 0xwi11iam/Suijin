"""File locking for cross-PROCESS shared stores (the multi-window fix).

The workspace is shared by every concurrent session: two terminal windows
running engagements both read-modify-write the knowledge graph and the
per-target memory. Plain write_text races there — last writer wins, and
the loser's findings silently vanish. flock + atomic replace closes both
holes: the lock serializes the read-modify-write critical section, the
tmp→os.replace swap means readers never see a torn file.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path
from typing import Iterator


@contextlib.contextmanager
def locked(path: Path | str) -> Iterator[None]:
    """Hold an exclusive advisory lock keyed to `path` (a `<name>.lock`
    sibling). Cross-process safe; reentrant-safe by pairing (same process
    re-locking the same file would self-deadlock — callers keep critical
    sections non-nesting)."""
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def atomic_write(path: Path | str, text: str) -> None:
    """Write text so readers see either the old file or the new one —
    never a partial write."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def locked_write(path: Path | str, render) -> None:
    """Serialize a full read-modify-write: hold the lock, let `render`
    produce the new text, swap atomically. `render` may re-read the file
    under the lock (the race-free reload)."""
    with locked(path):
        atomic_write(path, render())
