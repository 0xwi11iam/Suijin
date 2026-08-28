#!/usr/bin/env python3
"""tui_drive — the interactive review rig.

Spawns a REAL engagement under a REAL PTY so the AI (or operator) can
drive the live TUI with raw keystrokes and read actual frames back.

  launch:  .venv/bin/python scripts/tui_drive.py --target lab:blue_target
           [--provider real|fake] [--max-iters N] [--objective "..."]
           [--ws DIR] (isolated workspace for reproducible runs)

  drive (any shell, works across tool calls):
           printf 'focus on /admin\r'  > /tmp/suijin_drive/in.pipe
           printf '\x1b\x1b'           > /tmp/suijin_drive/in.pipe   # ESC ESC
           printf '\t'                 > /tmp/suijin_drive/in.pipe   # Tab (mode)
           printf '/quit\r'            > /tmp/suijin_drive/in.pipe

  read:    .venv/bin/python scripts/tui_drive.py --screen [--lines 50]
           tail -c 4000 /tmp/suijin_drive/out.log                   # raw
           cat /tmp/suijin_drive/exit.json                           # outcome

fake provider: a scripted responder streams reasoning+content deltas and
returns real JSON decisions (http_request -> write_note -> complete) —
zero network, deterministic, exercises the typewriter/boxes/loop for real.
"""

from __future__ import annotations

import argparse
import json
import os
import pty
import re
import select
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUN_DIR = Path("/tmp/suijin_drive")


# ── helpers ────────────────────────────────────────────────────────────


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_up(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def boot_lab(name: str) -> tuple[subprocess.Popen | None, int | None]:
    """lab:NAME -> (proc, port); the app gets a free PORT env."""
    apps = {
        "blue_target": REPO / "suijin" / "lab" / "blue_target" / "vulnerable_app.py",
        "hill_ctf": REPO / "suijin" / "lab" / "hill_ctf" / "app.py",
    }
    app = apps.get(name)
    if app is None or not app.is_file():
        print(f"unknown lab {name!r} (have: {', '.join(apps)})")
        return None, None
    port = _free_port()
    env = dict(os.environ, PORT=str(port))
    proc = subprocess.Popen(
        [sys.executable, str(app)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(REPO),
    )
    if not _wait_up(port):
        proc.kill()
        print(f"lab {name} failed to boot on :{port}")
        return None, None
    return proc, port


# ── the fake provider (child-side) ─────────────────────────────────────


def install_fake_provider() -> None:
    """Scripted generate(): streams via on_delta, returns real decisions."""
    import suijin.modules.providers.lib as pl

    script = [
        # turn 1: recon request against the target (streams think + say)
        {
            "reasoning": "First move is a baseline fetch of the target root to fingerprint the stack and collect headers before any testing. ",
            "content": json.dumps(
                {
                    "action": "use_tool",
                    "tool_name": "http_request",
                    "tool_args": {"method": "GET", "url": "__TARGET__"},
                    "thought": "Baseline the target",
                    "reasoning": "Fingerprint before testing",
                }
            ),
        },
        # turn 2: note the result
        {
            "reasoning": "The baseline responded; record it as a progress note so the audit trail starts honestly. ",
            "content": json.dumps(
                {
                    "action": "use_tool",
                    "tool_name": "write_note",
                    "tool_args": {
                        "content": "Baseline fetch complete — engagement loop healthy.",
                        "success": True,
                        "category": "progress",
                    },
                    "thought": "Note the baseline",
                    "reasoning": "Keep the audit trail current",
                }
            ),
        },
        # turn 3: done
        {
            "reasoning": "The mechanics are verified; this scripted engagement ends here with a clean completion. ",
            "content": json.dumps(
                {"action": "complete", "completion_reason": "scripted rig run complete", "thought": "Done"}
            ),
        },
    ]
    state = {"i": 0}

    def fake_generate(messages, config=None, **kw):
        hold = float(os.environ.get("SUIJIN_DRIVE_SLOW") or 0)
        if hold:
            time.sleep(hold)  # hold the thinking spinner — pause-chord testing
        idx = min(state["i"], len(script) - 1)
        state["i"] += 1
        turn = dict(script[idx])
        turn["content"] = turn["content"].replace(
            "__TARGET__", str(os.environ.get("SUIJIN_DRIVE_TARGET", "http://127.0.0.1"))
        )
        on_delta = kw.get("on_delta")
        if on_delta:
            for word in turn["reasoning"].split(" "):
                on_delta("reasoning", word + " ")
                time.sleep(0.01)
            on_delta("content", turn["content"][:40])
            on_delta("content", turn["content"][40:])
        pl.USAGE["calls"] += 1
        return turn["content"]

    pl.generate = fake_generate
    pl.generate_with_failover = lambda messages, config=None, **kw: fake_generate(messages, config, **kw)


# ── child: the engagement itself ───────────────────────────────────────


def child_main(args, target_url: str) -> int:
    os.environ["SUIJIN_DRIVE_TARGET"] = target_url
    sys.path.insert(0, str(REPO))
    if args.ws:
        os.environ["SUIJIN_WORKSPACE"] = args.ws
    if args.provider == "fake":
        install_fake_provider()
    with open("/tmp/rig_child_debug.log", "w") as _dbg:
        _dbg.write(f"isatty={sys.stdin.isatty()}\n")  # checked BEFORE the engagement too
    from suijin.modules.redteam.lib.red.config_loader import load_config
    from suijin.modules.redteam.lib.redteamer import run_red_team

    config = load_config()
    config["max_iterations"] = args.max_iters
    config.setdefault("provider", "zai")
    if args.provider == "fake":
        config["fallback_providers"] = []
    objective = args.objective or f"Find and exploit vulnerabilities on {target_url} — authorized local lab engagement."
    rc = run_red_team(config, objective)
    return int(rc or 0)


# ── parent: the driver ─────────────────────────────────────────────────


def drive(args) -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    if args.slow:
        os.environ["SUIJIN_DRIVE_SLOW"] = str(args.slow)
    fifo = RUN_DIR / "in.pipe"
    out_log = RUN_DIR / "out.log"
    with contextlib_suppress():
        fifo.unlink()
    os.mkfifo(fifo)

    lab_proc, port = (None, None)
    target = args.target
    if target.startswith("lab:"):
        lab_proc, port = boot_lab(target[4:])
        if lab_proc is None:
            return 1
        target = f"http://127.0.0.1:{port}"

    print(f"[drive] target={target} provider={args.provider} fifo={fifo} log={out_log}")
    pid, master = pty.fork()
    if pid == 0:  # child: the engagement, on the pty
        rc = 1
        try:
            rc = child_main(args, target)
        finally:
            os._exit(rc)

    fifo_fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    started = time.monotonic()
    with out_log.open("wb") as log:
        while True:
            try:
                r, _, _ = select.select([master, fifo_fd], [], [], 0.2)
            except OSError:
                break
            if master in r:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    data = b""
                if not data:
                    # child exited — fall through to the reap below
                    with contextlib_suppress():
                        os.waitpid(pid, 0)
                    (RUN_DIR / "exit.json").write_text(
                        json.dumps({"exit_status": "eof", "seconds": round(time.monotonic() - started, 1)})
                    )
                    break
                log.write(data)
                log.flush()
            if fifo_fd in r:
                try:
                    keys = os.read(fifo_fd, 4096)
                except BlockingIOError:
                    keys = b""
                if keys:
                    with contextlib_suppress():
                        (RUN_DIR / "fifo.log").open("ab").write(keys)  # delivery debug
                    try:
                        os.write(master, keys)
                    except OSError:
                        break
            done_pid, status = os.waitpid(pid, os.WNOHANG)
            if done_pid == pid:
                # drain the last frames
                time.sleep(0.2)
                with contextlib_suppress():
                    while True:
                        data = os.read(master, 65536)
                        if not data:
                            break
                        log.write(data)
                (RUN_DIR / "exit.json").write_text(
                    json.dumps({"exit_status": status, "seconds": round(time.monotonic() - started, 1)})
                )
                break
    os.close(fifo_fd)
    if lab_proc is not None:
        lab_proc.terminate()
    print(
        f"[drive] engagement finished ({(RUN_DIR / 'exit.json').read_text() if (RUN_DIR / 'exit.json').is_file() else '?'})"
    )
    return 0


class contextlib_suppress:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return True


_ANSI = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-B]|\r")


def show_screen(lines: int) -> None:
    if not (RUN_DIR / "out.log").is_file():
        print("no out.log — launch a run first")
        return
    raw = (RUN_DIR / "out.log").read_bytes()
    txt = _ANSI.sub(b"", raw).decode("utf-8", "replace")
    rows = [ln for ln in txt.split("\n") if ln.strip()]
    print("\n".join(rows[-lines:]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", default="lab:blue_target", help="lab:blue_target | lab:hill_ctf | http://host:port")
    ap.add_argument("--provider", choices=["real", "fake"], default="real")
    ap.add_argument("--max-iters", type=int, default=8, help="iteration cap (real-LLM cost guard)")
    ap.add_argument("--objective", default="")
    ap.add_argument("--ws", default="", help="isolated SUIJIN_WORKSPACE dir (omit for the real one)")
    ap.add_argument("--screen", action="store_true", help="print the ANSI-stripped tail of the last run")
    ap.add_argument("--lines", type=int, default=50)
    ap.add_argument(
        "--slow", type=float, default=0, metavar="SECS", help="fake provider holds each turn N secs (pause testing)"
    )
    args = ap.parse_args()
    if args.screen:
        show_screen(args.lines)
        return 0
    return drive(args)


if __name__ == "__main__":
    raise SystemExit(main())
