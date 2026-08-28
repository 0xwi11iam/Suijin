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

import re
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
RUN = Path("/tmp/suijin_drive")  # the driver's fixed control dir
PY = REPO / ".venv" / "bin" / "python"


@pytest.fixture()
def rig(tmp_path):
    RUN.mkdir(parents=True, exist_ok=True)
    for name in ("out.log", "fifo.log", "exit.json"):
        (RUN / name).unlink(missing_ok=True)
    env = {"SUIJIN_WORKSPACE": str(tmp_path), "PATH": "/usr/bin:/bin"}
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
    )
    fifo = RUN / "in.pipe"
    for _ in range(100):
        if fifo.exists():
            break
        time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("rig did not boot (no in.pipe)")
    yield fifo
    proc.kill()
    proc.wait(timeout=5)
    subprocess.run(["pkill", "-f", "vulnerable_app"], check=False)


def _log_text() -> str:
    raw = (RUN / "out.log").read_bytes() if (RUN / "out.log").is_file() else b""
    return re.sub(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r", b"", raw).decode("utf-8", "replace")


def _send(fifo, payload: bytes):
    with fifo.open("wb") as f:
        f.write(payload)


@pytest.mark.slow
def test_pause_chord_commands_and_resume(rig):
    """The operator's exact sequence, end to end."""
    fifo = rig
    time.sleep(5)  # engagement boots, turn 1 in flight (6s hold)

    # ESC ESC mid-think: instant PAUSED + banner — the LLM is STILL stuck
    _send(fifo, b"\x1b\x1b")
    time.sleep(2)
    out = _log_text()
    assert "Paused" in out, "no pause banner within 2s of the chord"
    assert "PAUSED" in out, "strip did not flip to PAUSED"

    # a command answers INSTANTLY during the stuck window
    _send(fifo, b"/cost\r")
    time.sleep(2)
    assert "calls" in _log_text(), "/cost did not answer during pause"

    # guidance queues (the LLM call is still holding)
    _send(fifo, b"focus on the login flow\r")
    time.sleep(2)
    assert "guidance queued" in _log_text(), "guidance not consumed by the session"

    # the turn ends -> main lands -> guidance injects -> the loop RESUMES
    deadline = time.time() + 25
    while time.time() < deadline:
        if "Guidance sent. Resuming" in _log_text():
            break
        time.sleep(1)
    assert "Guidance sent. Resuming" in _log_text(), "engagement did not resume after the turn"

    # the stream is live again (thinking spinner back, not PAUSED)
    time.sleep(3)
    assert "thinking" in _log_text()
