"""The exact path a person walks, in a real terminal:

    suijin  ->  menu  ->  Red Team  ->  objective  ->  the Rich TUI

Everything is hermetic: a throwaway workspace, a local OpenAI-compatible
stub for the provider, a pty for the terminal. No network, no keys, no
tokens spent, no operator state touched.

The pty is driven from a FRESH interpreter (a generated driver script
run as a subprocess), not from pytest's own process: forkpty() in a
process that pytest may have threaded deadlocks the child, and a
terminal test that silently sees an empty screen is worse than no test.

The stub never calls `complete`, so the run ends on the graph's recursion
limit — deliberate: it puts the TUI through a REAL termination (panel,
crash log, resumable bundle) instead of a happy-path mock.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = str(Path(__file__).resolve().parents[3])
ANSI = r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][B0]|\r"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="posix pty")

DRIVER = r"""
import json, os, pty, re, select, signal, socket, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO, TMP, PORT, SCREEN = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
ANSI = re.compile("\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][B0]|\r")


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        body = "".join(
            "data: " + json.dumps({"choices": [{"delta": {"content": c}, "finish_reason": None}]}) + "\n\n"
            for c in "The Rich TUI is live."
        ) + "data: [DONE]\n\n"
        raw = body.encode()
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


ws = TMP / "ws"
ws.mkdir(parents=True, exist_ok=True)
cfg = ws / "config.json"
cfg.write_text(json.dumps({
    "provider": "custom:lab",
    "custom:lab_model": "stub-1",
    "custom_providers": [{"name": "lab", "base_url": f"http://127.0.0.1:{PORT}/v1", "api_key": "stub"}],
    "supervision_enabled": False,
    "oracle_enabled": False,
    "drift_detection_enabled": False,
}))
boot = TMP / "boot.py"
boot.write_text(
    "import sys\n"
    f"sys.path.insert(0, {REPO!r})\n"
    "from pathlib import Path\n"
    "from suijin.modules.platform.lib import config_loader as cl\n"
    f"cl.CONFIG_PATH = Path({str(cfg)!r})\n"
    "import suijin.main\n"
    "suijin.main.main()\n"
)

# fork FIRST (single-threaded), start the stub AFTER
pid, fd = pty.fork()
if pid == 0:
    os.chdir(REPO)
    os.environ.update(TERM="xterm-256color", COLUMNS="120", LINES="40", SUIJIN_WORKSPACE=str(ws))
    home = ws / "home"
    home.mkdir(exist_ok=True)
    os.environ["HOME"] = str(home)
    os.execv(sys.executable, [sys.executable, str(boot)])

srv = HTTPServer(("127.0.0.1", PORT), Stub)
threading.Thread(target=srv.serve_forever, daemon=True).start()

out = bytearray()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if fd in r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            out.extend(chunk)


def wait(probe, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        if probe in ANSI.sub("", out.decode("utf-8", "replace")):
            return True
        pump(0.3)
    return False


steps = []
for label, probe, keys in (
    ("menu", "Select Operational", b"1\r"),
    ("objective menu", "Type manually", b"1\r"),
    ("objective prompt", "Objective", b"a stub target\r"),
    ("model reply", "The Rich TUI is live", None),
):
    if not wait(probe):
        steps.append(f"MISSING {label} ({probe!r})")
        break
    steps.append(f"ok {label}")
    if keys:
        os.write(fd, keys)

pump(8)  # let it reach a termination
try:
    os.kill(pid, signal.SIGKILL)
    os.close(fd)
except OSError:
    pass
srv.shutdown()

clean = ANSI.sub("", out.decode("utf-8", "replace"))
Path(SCREEN).write_text(clean, encoding="utf-8")
print("; ".join(steps))
"""


def _free_port():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def driven(tmp_path_factory):
    """Run the user path once; yield (screen, workspace)."""
    tmp = tmp_path_factory.mktemp("userpath")
    driver = tmp / "driver.py"
    driver.write_text(DRIVER, encoding="utf-8")
    screen = tmp / "screen.txt"
    port = _free_port()
    proc = subprocess.run(
        [sys.executable, str(driver), REPO, str(tmp), str(port), str(screen)],
        capture_output=True,
        text=True,
        timeout=420,
    )
    text = screen.read_text(encoding="utf-8") if screen.exists() else ""
    return text, tmp / "ws", proc.stdout.strip(), proc.stderr[-800:]


@pytest.mark.slow
def test_suijin_menu_red_team_reaches_the_rich_tui(driven):
    """suijin -> 1 -> 1 -> objective -> the Rich TUI, end to end."""
    screen, _ws, steps, err = driven
    assert screen, f"the child produced no screen at all ({steps}) {err}"
    assert "MISSING" not in steps, f"{steps}\n--- screen tail ---\n{screen[-2500:]}"
    assert "SUIJIN Mode Selector" in screen
    assert "Red Team" in screen
    assert "The Rich TUI is live" in screen, "the model reply never rendered"
    # the engagement UI, not a bare print
    assert "recon" in screen, "the TUI status line never appeared"
    assert "Traceback" not in screen, "the TUI raised while rendering"
    # a real termination, not silence
    assert any(w in screen for w in ("ERROR", "COMPLETE", "STOPPED", "FAILED")), "no termination panel"
    # no keypress gate before the menu
    assert "Press Enter to continue" not in screen.split("Select Operational")[0], (
        "a keypress still stands between suijin and the menu"
    )


@pytest.mark.slow
@pytest.mark.slow
def test_a_ended_run_is_saved_and_returns_to_the_menu(driven):
    """A finished engagement leaves a resumable .sje and a journal, and
    the console lands back on the menu — the run is never a dead end and
    never a lost one."""
    screen, ws, _steps, _err = driven
    bundles = list(ws.rglob("*.sje"))
    assert bundles, "the run ended without a .sje — it cannot be resumed"
    assert list(ws.rglob("events.jsonl")), "no journal — the run left no trace"
    # back at the menu, ready for the next one
    assert screen.rstrip().endswith("4. Exit") or screen.count("Select Operational Module") >= 2, (
        "the console did not return to the menu after the run ended"
    )


def test_the_crash_log_lands_in_outputs_logs():
    """The crash panel promises outputs/logs/engage_crash.log — that is
    where it must actually be written (it used to go to <ws>/logs, where
    the operator would never find it)."""
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR, logs_dir

    assert logs_dir() == WORKSPACE_DIR / "outputs" / "logs"
    assert logs_dir().is_dir()

    import inspect

    from suijin.modules.redteam.lib import redteamer

    src = inspect.getsource(redteamer)
    assert 'WORKSPACE_DIR / "logs"' not in src, "a crash handler still writes to the wrong dir"
    assert src.count("logs_dir()") >= 2, "both crash handlers should use logs_dir()"
