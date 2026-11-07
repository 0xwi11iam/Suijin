"""box manager — the curses hypervisor view (selection list, live).

Curses because it's a live selection dashboard (the same pattern as
the swarm console); the surrounding menus are Rich. Actions here all
have CLI twins (sandbox ls/logs/stop/shell, box image build).
"""

from __future__ import annotations

import contextlib
import curses
import re
import subprocess
import time


def _line(stdscr, y, x, txt, attr=0):
    with contextlib.suppress(Exception):
        stdscr.addstr(y, x, txt[: max(1, stdscr.getmaxyx()[1] - x - 1)], attr)


def _boxes() -> list[dict]:
    from suijin.server.box import hypervisor as hv

    return hv.sandbox_ls()


def _box_manager(stdscr) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    curses.start_color()
    curses.use_default_colors()
    for i, fg in ((1, curses.COLOR_GREEN), (2, curses.COLOR_YELLOW), (3, curses.COLOR_RED),
                  (4, curses.COLOR_CYAN)):
        curses.init_pair(i, fg, -1)
    sel = 0
    detail: list = []
    detail_for = ""
    while True:
        stdscr.erase()
        maxy, maxx = stdscr.getmaxyx()
        from suijin.server.box import hypervisor as hv

        boxes = _boxes()
        pool_n = len(hv.pool_members())
        sel = min(sel, max(0, len(boxes) - 1))
        fresh = hv.image_is_fresh() if hv.image_exists() else None

        _line(stdscr, 0, 2, " boxes — the hypervisor view ", curses.A_REVERSE | curses.A_BOLD)
        mark = "fresh" if fresh else ("stale — [i] rebuild" if fresh is False else "not built")
        _line(stdscr, 2, 2, f"image {hv.image_tag()}  [{mark}]", curses.color_pair(2) if fresh is False else curses.A_DIM)
        _line(stdscr, 3, 2, f"warm pile: {pool_n} idle box(es) — EMPTY containers, no agent, no intent;", curses.A_DIM)
        _line(stdscr, 4, 2, "the next spawn turns one into an agent in ~0.1s", curses.A_DIM)
        _line(stdscr, 5, 2, f"{'NAME':46s} {'STATE':8s} {'AGENT':24s} UP", curses.A_UNDERLINE)
        for i, b in enumerate(boxes[: max(1, maxy - 12)]):
            m = ">" if i == sel else " "
            state = "up" if "up" in b["status"].lower() else b["status"].split()[0].lower()
            pooled = "pool" in b["name"]
            agent = "(warm — empty)" if pooled else re.sub(r"^suijin-sbx-[^-]+-", "", b["name"])[:20]
            color = curses.color_pair(1) if state == "up" else curses.A_DIM
            attr = color | (curses.A_BOLD if i == sel else 0)
            _line(stdscr, 6 + i, 2,
                  f"{m}{b['name'][:44]:44s} {state:8s} {agent:24s} {b['age'][:14]}", attr)

        name = boxes[sel]["name"] if boxes else ""
        if name != detail_for:
            detail_for = name
            with contextlib.suppress(Exception):
                st = subprocess.run(["docker", "inspect", name, "--format", "{{.State.Status}}"],
                                    capture_output=True, text=True, timeout=10)
                state_word = (st.stdout.strip() or "gone").lower()
                detail = [f"container: {state_word}" + (" (warm pile — starts on demand)" if "pool" in name else "")]
                if "pool" not in name and state_word == "running":
                    log = subprocess.run(["docker", "logs", "--tail", "8", name],
                                         capture_output=True, text=True, timeout=10).stdout
                    for ln in log.splitlines()[-6:]:
                        detail.append(f"  {ln[:78]}")
        dy = maxy - (len(detail) + 2)
        _line(stdscr, dy - 1, 2, "-" * max(10, maxx - 4), curses.A_DIM)
        _line(stdscr, dy, 2, f"box: {name[:40]}  [l]ogs [s]hell [k]ill [b]ack", curses.A_UNDERLINE)
        for i, d in enumerate(detail):
            if dy + 1 + i < maxy - 1:
                _line(stdscr, dy + 1 + i, 2, f"  {d}", curses.A_DIM)
        _line(stdscr, maxy - 1, 2, " up/down select · b back ", curses.A_REVERSE)
        stdscr.refresh()

        key = stdscr.getch()
        if key in (ord("b"), ord("B"), 27, ord("q")):
            return
        if key == curses.KEY_DOWN:
            sel = min(sel + 1, max(0, len(boxes) - 1))
        elif key == curses.KEY_UP:
            sel = max(sel - 1, 0)
        elif key in (ord("k"), ord("K")) and boxes:
            with contextlib.suppress(Exception):
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        elif key in (ord("l"), ord("L"), curses.KEY_ENTER, 10) and boxes:
            with contextlib.suppress(Exception):
                logs = subprocess.run(["docker", "logs", "--tail", "30", name],
                                      capture_output=True, text=True, timeout=15).stdout
            stdscr.erase()
            _line(stdscr, 0, 2, f"logs — {name} (any key = back)", curses.A_UNDERLINE)
            for i, ln in enumerate(logs.splitlines()[-(maxy - 2):]):
                _line(stdscr, 1 + i, 2, ln[: max(1, maxx - 3)])
            stdscr.refresh()
            stdscr.nodelay(True)
            while stdscr.getch() == -1:
                curses.napms(100)
        elif key in (ord("s"), ord("S")) and boxes:
            curses.endwin()
            subprocess.call(["docker", "exec", "-it", name, "bash"], stdin=None)
            curses.doupdate()
        curses.napms(150)


def run_box_manager_cmd() -> int:
    import sys

    if not sys.stdout.isatty():
        print("box manager needs a terminal (tty).")
        return 1
    return curses.wrapper(_box_manager)
