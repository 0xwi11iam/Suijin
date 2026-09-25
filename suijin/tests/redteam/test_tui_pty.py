"""PTY integration — the REAL engagement under a REAL pseudo-terminal.

Drives scripts/tui_drive.py (fake scripted LLM, real TUI stack: cbreak
reader, Live strip, typewriter, pause session) through the operator's
exact breakage sequence: ESC ESC mid-think -> instant PAUSED + commands
answer during the stuck window -> guidance queues -> resume after the
turn ends. This is the test class that caught the prompt-eating bug
(apply_key dropped the buffer) and the text-layer buffering bug that
killed the ESC ESC chord.

Marked slow: spawns processes, takes ~40s.
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PY = Path(sys.executable)  # works everywhere (local venv AND CI system python)


@pytest.fixture()
def rig(tmp_path):
    """One private control dir per test, and teardown that only ever
    touches this test's own process tree.

    It used to share /tmp/suijin_drive with every other run on the
    machine (unlinking another run's fifo/log out from under it) and
    finish with `pkill -f vulnerable_app`, which kills ANY process
    matching that name — including a lab app the developer had running
    on the side, and it hard-failed teardown wherever pkill is absent.
    """
    run_dir = tmp_path / "drive"
    run_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "SUIJIN_WORKSPACE": str(tmp_path),
        "SUIJIN_DRIVE_DIR": str(run_dir),
        "PATH": "/usr/bin:/bin",
    }
    proc = subprocess.Popen(
        [
            str(PY),
            str(REPO / "scripts" / "tui_drive.py"),
            "--target",
            "lab:blue_target",
            "--provider",
            "fake",
            "--slow",
            "6",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(REPO),
        start_new_session=True,  # its own group, so cleanup is exact
    )
    fifo = run_dir / "in.pipe"
    for _ in range(100):
        if fifo.exists():
            break
        time.sleep(0.2)
    else:
        _kill_tree(proc)
        pytest.fail("rig did not boot (no in.pipe)")
    yield fifo, run_dir
    _kill_tree(proc)


def _kill_tree(proc):
    """SIGTERM then SIGKILL this child's own process group — and nothing
    else on the machine."""
    import signal as _signal

    for sig, grace in ((_signal.SIGTERM, 3.0), (_signal.SIGKILL, 1.0)):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.send_signal(sig)
            except (ProcessLookupError, OSError):
                return
        try:
            proc.wait(timeout=grace)
            return
        except Exception:
            continue


def _log_text(run_dir) -> str:
    raw = (run_dir / "out.log").read_bytes() if (run_dir / "out.log").is_file() else b""
    return re.sub(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r", b"", raw).decode("utf-8", "replace")


def _send(fifo, payload: bytes):
    with fifo.open("wb") as f:
        f.write(payload)


@pytest.mark.slow
def test_pause_chord_commands_and_resume(rig):
    """The operator's exact sequence, end to end."""
    fifo, run_dir = rig
    time.sleep(5)  # engagement boots, turn 1 in flight (6s hold)

    # /pause mid-think: instant PAUSED + banner — the LLM is STILL stuck
    # (ESC ESC was removed 2026-09-23: the chord was a landmine)
    _send(fifo, b"/pause\r")
    time.sleep(5)  # timeout-poll is 2s; allow a full poll + processing
    out = _log_text(run_dir)
    assert "Paused" in out, "no pause banner within 5s of the chord"
    assert "PAUSED" in out, "strip did not flip to PAUSED"

    # a command answers INSTANTLY during the stuck window
    _send(fifo, b"/cost\r")
    time.sleep(2)
    assert "calls" in _log_text(run_dir), "/cost did not answer during pause"

    # guidance queues (the LLM call is still holding)
    _send(fifo, b"focus on the login flow\r")
    time.sleep(2)
    assert "guidance queued" in _log_text(run_dir), "guidance not consumed by the session"

    # the turn ends -> main lands -> guidance written to file -> resume
    deadline = time.time() + 25
    while time.time() < deadline:
        if "guidance written" in _log_text(run_dir) or "Resuming" in _log_text(run_dir):
            break
        time.sleep(1)
    assert "guidance written" in _log_text(run_dir) or "Resuming" in _log_text(run_dir), (
        "engagement did not resume after the turn"
    )

    # the stream is live again (thinking spinner back, not PAUSED)
    time.sleep(3)
    assert "thinking" in _log_text(run_dir)
