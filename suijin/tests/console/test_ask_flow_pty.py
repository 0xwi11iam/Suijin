"""Ask-operator flow, driven in a real pty against a scripted provider.

Field complaint: "ask operator question is kinda fucked up". Confirmed by
driving it: the question rendered as a bare orphan line far above an
EMPTY panel whose body was the literal word "Answer" — the operator had
no idea what they were answering. Two turns: (1) the model asks; (2) only
after the operator's answer reaches its context does it complete.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = str(Path(__file__).resolve().parents[3])
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][B0]|\r")

pytestmark = [pytest.mark.slow, pytest.mark.skipif(os.name == "nt", reason="posix pty")]

DRIVER = r"""
import json, os, pty, re, select, signal, socket, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO, PORT, OUT = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
ANSI = re.compile("\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][B0]|\r")
TMP = Path(tempfile.mkdtemp(prefix="asktest-"))
(TMP / "ws").mkdir()


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace")
        if "OPERATOR ANSWER" in body or "OPERATOR-CONFIRMED" in body:
            d = '{"action":"complete","completion_reason":"Objective-complete: answered."}'
        else:
            d = ('{"action":"use_tool","tool_name":"ask_operator","tool_args":{"question":'
                 '"Which exact host is the approved target?"},"thought":"no target in order"}')
        raw = ("data: " + json.dumps({"choices": [{"delta": {"content": d}}]}) + "\n\n" + "data: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


srv = HTTPServer(("127.0.0.1", PORT), Stub)
threading.Thread(target=srv.serve_forever, daemon=True).start()
cfg = TMP / "config.json"
cfg.write_text(json.dumps({
    "provider": "custom:lab", "custom:lab_model": "stub-1",
    "custom_providers": [{"name": "lab", "base_url": f"http://127.0.0.1:{PORT}/v1", "api_key": "s"}],
    "supervision_enabled": False, "oracle_enabled": False, "drift_detection_enabled": False,
}))
boot = TMP / "boot.py"
boot.write_text(f"import sys\nsys.path.insert(0, {REPO!r})\nfrom pathlib import Path\n"
                f"from suijin.modules.platform.lib import config_loader as cl\ncl.CONFIG_PATH = Path({str(cfg)!r})\n"
                "import suijin.main\nsuijin.main.main()\n")

pid, fd = pty.fork()
if pid == 0:
    os.chdir(REPO)
    os.environ.update(TERM="xterm-256color", COLUMNS="120", LINES="40", SUIJIN_WORKSPACE=str(TMP / "ws"))
    (TMP / "ws" / "home").mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(TMP / "ws" / "home")
    os.execv(sys.executable, [sys.executable, str(boot)])

out = bytearray()


def pump(s):
    end = time.time() + s
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if fd in r:
            try:
                c = os.read(fd, 65536)
            except OSError:
                return
            out.extend(c) if c else None


def wait(probe, seconds=90):
    end = time.time() + seconds
    while time.time() < end:
        if probe in ANSI.sub("", out.decode("utf-8", "replace")):
            return True
        pump(0.3)
    return False


steps = []
try:
    if wait("Select Operational"):
        os.write(fd, b"1\r")
        if wait("Type manually"):
            os.write(fd, b"1\r")
            if wait("Objective"):
                os.write(fd, b"a stub target\r")
                steps.append("ask panel" if wait("agent question") else "NO PANEL")
                pump(2)
                os.write(fd, b"www.example.com\r")
                steps.append("answer sent" if wait("Answer sent") else "NO ANSWER ACK")
                steps.append("done" if (wait("COMPLETE", 40) or wait("Objective-complete", 10)) else "NO COMPLETE")
finally:
    try:
        os.kill(pid, signal.SIGKILL)
        os.close(fd)
    except OSError:
        pass
    srv.shutdown()
OUT.write_text(ANSI.sub("", out.decode("utf-8", "replace")), encoding="utf-8")
print(";".join(steps))
"""


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_ask_flow_end_to_end(tmp_path):
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER, encoding="utf-8")
    screen_path = tmp_path / "screen.txt"
    proc = subprocess.run(
        [sys.executable, str(driver), REPO, str(_free_port()), str(screen_path)],
        capture_output=True,
        text=True,
        timeout=420,
    )
    steps = proc.stdout.strip()
    assert "NO PANEL" not in steps and "NO ANSWER ACK" not in steps and "NO COMPLETE" not in steps, (
        f"flow broke: {steps}\n--- tail ---\n{screen_path.read_text()[-2000:] if screen_path.exists() else proc.stderr[-800:]}"
    )
    screen = screen_path.read_text(encoding="utf-8")
    # THE fix: the question text renders INSIDE the agent-question panel
    i = screen.find("agent question")
    assert i > 0, "no ask panel rendered"
    window = screen[i : i + 1200]
    assert "Which exact host is the approved target?" in window, (
        f"the panel body must carry the question, got: {window[:300]!r}"
    )
    # the strip never wraps to a second row (ghost-fragment source)
    for line in screen.splitlines():
        if "CRED" in line and "LOW" in line:
            assert not line.strip().endswith("|"), f"strip wrapped: {line!r}"
