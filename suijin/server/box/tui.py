"""box tui — the container manager, per the approved design.

Design contract (2026-10-20, operator rulings):
- lowercase everywhere — no title case, no all-caps
- color + bold carry meaning: green ok · yellow drift/pending · red
  failed/destructive · dim secondary — glyphs are ascii only
- progress = block bars with ascii fallback — no emojis, no spinners
- clean fit at 80 columns, compact mode below 60
- both arrow-sequence families handled (the settings-tui lesson)
- typed confirmation only where data dies (destroy)
"""

from __future__ import annotations

import contextlib
import curses
import time

WIDTH = 80
COMPACT = 60

# status words + colors ---------------------------------------------------
OK = ("ok", curses.COLOR_GREEN)
PEND = ("!", curses.COLOR_YELLOW)
FAIL = ("x", curses.COLOR_RED)
NEUTRAL = ("o", -1)


def _bar(frac: int | float, width: int = 24, ascii_only: bool = False) -> str:
    frac = max(0.0, min(1.0, float(frac)))
    full = int(round(frac * width))
    if ascii_only:
        return "#" * full + "-" * (width - full)
    return "█" * full + "░" * (width - full)


def _get_key(stdscr) -> str:
    """One keypress; both arrow families, esc-safe (settings lesson)."""
    k = stdscr.getch()
    if k == curses.KEY_UP:
        return "up"
    if k == curses.KEY_DOWN:
        return "down"
    if k == curses.KEY_LEFT:
        return "left"
    if k == curses.KEY_RIGHT:
        return "right"
    if k == curses.KEY_ENTER or k == 10 or k == 13:
        return "enter"
    if k == 27:  # esc — check for a [ / O sequence without blocking long
        with contextlib.suppress(Exception):
            stdscr.nodelay(True)
            n1 = stdscr.getch()
            if n1 in (ord("["), ord("O")):
                n2 = stdscr.getch()
                stdscr.nodelay(False)
                return {ord("A"): "up", ord("B"): "down", ord("C"): "right", ord("D"): "left"}.get(n2, "esc")
            stdscr.nodelay(False)
        return "esc"
    if k == ord(" "):
        return "space"
    if k >= 0 and chr(k).isprintable():
        return chr(k).lower()
    return ""


class Tui:
    """Shared chrome: border, colored status, cta rows, messages."""

    def __init__(self, stdscr):
        self.s = stdscr
        self.rows, self.cols = stdscr.getmaxyx()
        self.compact = self.cols < COMPACT
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        for i, (name, color) in enumerate(
            (
                ("g", curses.COLOR_GREEN),
                ("y", curses.COLOR_YELLOW),
                ("r", curses.COLOR_RED),
                ("c", curses.COLOR_CYAN),
                ("d", -1),
            ),
            start=1,
        ):
            curses.init_pair(i, color, -1)
        self.cg = curses.color_pair(1)
        self.cy = curses.color_pair(2)
        self.cr = curses.color_pair(3) | curses.A_BOLD
        self.cc = curses.color_pair(4)
        self.cd = curses.A_DIM
        self.cb = curses.A_BOLD

    # ── primitives ────────────────────────────────────────────────
    def line(self, y, x, text, attr=0):
        if 0 < y < self.rows - 1 and x < self.cols - 1:
            with contextlib.suppress(curses.error):
                self.s.addstr(y, x, text[: self.cols - 1 - x], attr)

    def frame(self, title: str):
        self.s.erase()
        if not self.compact:
            with contextlib.suppress(curses.error):
                self.s.border()
        t = f" {title} "
        self.line(0, 2, t, self.cb | self.cc)

    def cta(self, keys: list[tuple[str, str]], row: int | None = None):
        """bottom cta row: [(key, label), ...] — bold keys."""
        y = self.rows - 2 if row is None else row
        x = 2
        for k, label in keys:
            self.line(y, x, f"[{k}]", self.cb)
            x += len(k) + 2
            self.line(y, x, f" {label}", 0)
            x += len(label) + 4
            if x > self.cols - 12:
                y += 1
                x = 2

    def status(self, y, x, kind, word, extra=""):
        color = {OK[0]: self.cg, PEND[0]: self.cy, FAIL[0]: self.cr, NEUTRAL[0]: self.cd}.get(kind, 0)
        self.line(y, x, kind, color)
        self.line(y, x + len(kind) + 1, word, color)
        if extra:
            self.line(y, x + len(kind) + 1 + len(word) + 1, extra, self.cd)

    # ── screens ───────────────────────────────────────────────────
    def first_run(self) -> str:
        """First-run screen WITH a real engine picker: up/down arrows
        select among the available engines (the pick persists to
        container.engine immediately), b/enter builds with the choice.
        Before this the screen just SHOWED the autodetect result — there
        was no way to pick the other engine from the tui at all."""
        from suijin.server.box import engine as bx

        detected = bx.detect_engine()
        avail: list[str] = []
        if bx._orb_available():  # mac-only, per the platform gate
            avail.append("orb")
        if _has("docker"):
            avail.append("docker")
        sel = detected if detected in avail else (avail[0] if avail else "")
        labels = {"orb": "orbstack (orb)", "docker": "docker"}
        while True:
            self.frame("suijin box")
            y = 3
            self.line(y, 2, "no box exists yet.", self.cb)
            y += 2
            self.line(y, 2, "a box is a sealed linux environment that runs suijin and")
            y += 1
            self.line(y, 2, "its tools. your device stays untouched, and engagements")
            y += 1
            self.line(y, 2, "live on the box's own disk.")
            y += 2
            self.line(y, 2, "engine", self.cb)
            if len(avail) > 1:
                self.line(y, 9, "up/down arrows change it — the pick persists", self.cd)
            y += 1
            for e in avail:
                mark = "ok" if e == sel else "o"
                word = labels[e] + ("  ←" if e == sel else "")
                self.status(y, 4, mark, word)
                y += 1
            if not avail:
                self.status(y, 4, "x", "no engine found", "— install orbstack or docker")
                y += 1
            y += 1
            self.line(y, 2, "switch any time with SUIJIN_CONTAINER_ENGINE or settings", self.cd)
            self.cta([("q", "quit")] + ([("b", "build the box")] if sel else []))
            self.s.refresh()
            k = _get_key(self.s)
            if k in ("up", "down") and len(avail) > 1:
                i = avail.index(sel)
                sel = avail[(i + (1 if k == "down" else -1)) % len(avail)]
                bx._persist_engine(sel)
            elif k in ("b", "enter") and sel:
                return "build"
            elif k in ("q", "esc"):
                return "quit"

    def dashboard(self, info: dict) -> str:
        self.frame("suijin box")
        y = 2
        if info["running"]:
            badge = " · adopted (yours — never deleted)" if info.get("adopted") else ""
            self.status(
                y, 2, "ok", "running", f"· {info['name']} · kali · up {info['uptime']} · {info['engine']}{badge}"
            )
        elif info["exists"]:
            self.status(y, 2, "o", "stopped", f"· {info['name']} · {info['engine']}")
        else:
            self.status(y, 2, "x", "not built")
        y += 1
        self.line(y, 2, "code", self.cb)
        self.line(y, 8, f"{info['code']} (live mount)" if info["exists"] else "—", self.cd)
        y += 1
        self.line(y, 2, "data", self.cb)
        self.line(y, 8, info["data"], self.cd)
        y += 1
        self.line(y, 2, "sync", self.cb)
        if info["running"]:
            if info["drift"] or info["missing"]:
                self.line(y, 8, "host → box ", 0)
                self.line(y, 19, f"! drift: {info['drift_summary']}", self.cy)
            else:
                self.line(y, 8, "host → box   in sync", self.cg)
            y += 1
            self.line(y, 8, "box → host   sealed — nothing flows back", self.cd)
        else:
            self.line(y, 8, "checked at enter", self.cd)
        y += 1
        self.line(y, 2, "tools", self.cb)
        if info.get("tools_total"):
            self.line(y, 8, f"{info['tools_ok']}/{info['tools_total']}", self.cb)
            if info["missing"]:
                self.line(
                    y,
                    8 + len(f"{info['tools_ok']}/{info['tools_total']}") + 1,
                    f"· {len(info['missing'])} pending",
                    self.cy,
                )
        else:
            # never a blank row: an unbuilt/mid-build box still answers
            self.line(y, 8, "… building" if info["running"] else "—", self.cd)
        if info["running"]:
            keys = [
                ("e", "enter"),
                ("r", "refresh"),
                ("b", "build"),
                ("t", "tools"),
                ("g", "data"),
                ("s", "stop"),
                ("d", "destroy…"),
                ("x", "settings"),
                ("q", "quit"),
            ]
        elif info["exists"]:
            keys = [("s", "start"), ("d", "destroy…"), ("x", "settings"), ("q", "quit")]
        else:
            keys = [("b", "build"), ("q", "quit")]
        self.cta(keys)
        self.s.refresh()
        while True:
            k = _get_key(self.s)
            valid = {kk for kk, _ in keys} | {"esc"}
            if k in valid:
                return k if k != "esc" else "quit"

    def build_screen(self, state: dict, out_tail: list) -> str | None:
        """Live build view. state: {stages:{name:{ok,at}}, tools:{},
        done,total,current}. Returns None to continue, 'pause'/'quit'."""
        self.frame("suijin box · build")
        names = [
            "base image",
            "system packages",
            "suijin (editable, live-mounted)",
            "root config → box",
            "security toolchain",
            "first-boot verify",
        ]
        y = 2
        for i, (key, label) in enumerate(
            zip(("base", "suijin", "profile", "config", "tools", "verify"), names, strict=True)
        ):
            st = state["stages"].get(key, {})
            num = f"{i + 1}/6"
            self.line(y, 2, num, self.cd)
            if key == "tools" and state.get("total"):
                frac = state.get("done", 0) / max(1, state["total"])
                # white while running, green when done, dim before it starts
                style = self.cg if st.get("ok") is True else (self.cb if st.get("ok") is None else self.cd)
                self.line(y, 8, label, style)
                self.line(y + 1, 8, _bar(frac, 24) + f"  {int(frac * 100)}%", self.cc)
                if state.get("current"):
                    elapsed = max(0.0, (state.get("tick") or 0) - (state.get("started_at") or 0))
                    self.line(y + 2, 8, f"!  {state['current']} · {elapsed:.1f}s", self.cy)
                    y += 3
                else:
                    y += 2
            elif st.get("ok") is True:
                self.status(y, 8, "ok", label, st.get("at", ""))  # green
            elif st.get("ok") is False:
                self.status(y, 8, "x", label, "— re-run resumes here")  # red
            elif st.get("ok") is None:
                # actually RUNNING right now (a start event landed): white
                self.line(y, 8, f"• {label}", self.cb)
            else:
                # not started: dim gray — never white on a guess
                self.line(y, 8, f"• {label}", self.cd)
            y += 1
        self.line(y + 1, 2, "─ output " + "─" * max(0, self.cols - 12), self.cd)
        # fill the whole bottom with output: the pane used to show a
        # fixed 5 lines above a wall of empty rows (operator 2026-10-24)
        top = y + 2
        bottom = self.rows - 3  # the cta owns rows-2
        n = max(0, bottom - top)
        for j, ln in enumerate(out_tail[-n:] if n else []):
            self.line(top + j, 2, ln[: self.cols - 4], self.cd)
        self.cta([("p", "pause after current tool"), ("esc", "cancel (kept)")], row=self.rows - 2)
        self.s.refresh()
        return None

    def enter_drift(self, d: dict) -> str:
        self.frame("enter · host changed since last sync")
        y = 2
        for c in d.get("changed", []):
            self.line(y, 2, "~ " + c, self.cy)
            y += 1
        miss = d.get("missing_tools") or []
        if miss:
            self.line(y, 2, f"tools  +{len(miss)} in the host list, not installed here", self.cb)
            y += 1
            self.line(y, 4, " · ".join(miss[:8]) + (" …" if len(miss) > 8 else ""), self.cd)
            y += 1
        y += 1
        self.cta([("f", "fix & enter"), ("j", "just enter"), ("c", "cancel")], row=y + 1)
        self.s.refresh()
        while True:
            k = _get_key(self.s)
            if k in ("f", "j", "c", "esc"):
                return k if k != "esc" else "c"

    def message(self, title: str, lines: list, keys=None, hold_s: float = 1.6) -> str:
        """Result flash: shows briefly (or until a listed key), then
        RETURNS so the caller redraws — every action ends in a redraw
        (operator 2026-10-24: 'in sync · nothing to do' used to sit on
        screen forever after pressing r)."""
        self.frame(title)
        y = 2
        for ln in lines:
            self.line(y, 2, ln)
            y += 1
        self.cta(keys or [("q", "done")], row=y + 1)
        self.s.refresh()
        with contextlib.suppress(Exception):
            self.s.timeout(int(hold_s * 1000))
            try:
                k = self.s.getch()
            finally:
                self.s.timeout(-1)
            if k in (-1, ord("q"), 27):
                return "q" if k == ord("q") else ""
            for kk, _ in keys or []:
                if k == ord(kk):
                    return kk
        return ""

    def confirm_destroy(self, info: dict) -> str:
        """typed confirmation — the only one in the app."""
        self.frame("destroy")
        if info.get("adopted"):
            self.line(2, 2, f"{info['name']} is YOUR container — it is kept.", self.cy)
            self.line(3, 2, "destroy removes only suijin's footprint inside it:", 0)
            self.line(4, 4, "/opt/suijin-ws · the toolchain · the profile dropin", self.cd)
            self.line(6, 2, "[a] archive engagements first, then clean   [c] cancel", self.cb)
            self.line(7, 2, f"[d] clean without archive — type: clean {info['name']}", self.cr)
        else:
            self.line(2, 2, "this deletes the box and its disk.", self.cr)
            self.line(4, 2, f"engagements on it: {info.get('engagements', '?')} ({info.get('size', '?')}).")
            self.line(6, 2, "[a] archive first, then destroy", self.cb)
            self.line(7, 4, "→ ~/suijn-box-archive-<date>.tar.gz", self.cd)
            self.line(8, 2, "[d] destroy without archive — type: destroy suijin-box", self.cr)
            self.line(9, 4, "> ", 0)
        self.s.refresh()
        typed = ""
        confirm_word = "clean" if info.get("adopted") else "destroy"
        expect = f"{confirm_word} {info['name']}"
        while True:
            k = _get_key(self.s)
            if k == "a":
                return "archive"
            if k == "esc" or k == "c":
                return "cancel"
            if k == "enter":
                if typed.strip() == expect:
                    return "destroy"
                typed = ""
                self.line(9, 4, "> " + " " * 30, 0)
                self.s.refresh()
            elif k and len(k) == 1:
                typed += k
                self.line(9, 4, "> " + typed, 0)
                self.s.refresh()


def _has(binary: str) -> bool:
    import shutil

    return shutil.which(binary) is not None


# ── the controller ─────────────────────────────────────────────────────


def _gather_info() -> dict:
    from suijin.server.box import engine as bx

    eng = bx.detect_engine()
    exists = bx.box_exists(eng) if eng else False
    running = bx.box_running(eng) if exists else False
    info = {
        "engine": eng or "none",
        "name": bx.box_name(),
        "adopted": bx.is_adopted(),
        "exists": exists,
        "running": running,
        "code": "-",
        "data": bx.BOX_WS,
        "drift": [],
        "missing": [],
        "drift_summary": "",
        "uptime": "",
        "tools_ok": 0,
        "tools_total": 0,
        "engagements": 0,
        "size": "",
    }
    with contextlib.suppress(Exception):
        info["code"] = _code_head()
    if exists:
        # the tools row must ALWAYS answer: ok/total from the box's own
        # state file (blank row = looks broken, operator 2026-10-24)
        with contextlib.suppress(Exception):
            import json as _json

            from suijin.server.box.sync import tool_list

            rc, out = bx.run_in_box(f"cat {bx.BOX_STATE} 2>/dev/null", timeout=30)
            tools = (_json.loads(out or "{}").get("tools") or {}) if rc == 0 else {}
            ok = sum(1 for v in tools.values() if isinstance(v, dict) and v.get("ok"))
            total = len(tool_list()) or len(tools)
            info["tools_ok"], info["tools_total"] = ok, total
    if running:
        with contextlib.suppress(Exception):
            from suijin.server.box.sync import drift as compute_drift

            d = compute_drift()
            info["drift"] = d["changed"]
            info["missing"] = d["missing_tools"]
            info["drift_summary"] = ", ".join(
                d["changed"] + [f"+{len(d['missing_tools'])} tools"] if d["missing_tools"] else []
            )
        with contextlib.suppress(Exception):
            rc, out = bx.run_in_box(
                f"ls {bx.BOX_WS}/engagements 2>/dev/null | wc -l; du -sh {bx.BOX_WS} 2>/dev/null | cut -f1",
                timeout=60,
            )
            parts = out.split()
            if len(parts) >= 2:
                info["engagements"], info["size"] = parts[0], parts[1]
    return info


def _code_head() -> str:
    from suijin.server.box import engine as bx

    rc, out = bx.run_in_box(
        "cd /opt/suijin 2>/dev/null || cd $(dirname $(find / -maxdepth 4 -name pyproject.toml -path '*suijin*' 2>/dev/null | head -1)); git rev-parse --short HEAD 2>/dev/null",
        timeout=60,
    )
    return out.strip()[:8] if rc == 0 else "?"


def run_tui() -> int:
    def _main(stdscr) -> int:
        t = Tui(stdscr)
        from suijin.server.box import engine as bx

        if not bx.box_exists():
            k = t.first_run()
            if k != "build":
                return 0
        while True:
            info = _gather_info()
            k = t.dashboard(info)
            if k in ("q", "esc"):
                return 0
            if k == "b":
                _run_build(t)
            elif k == "e":
                d = {"changed": info["drift"], "missing_tools": info["missing"]}
                choice = "j"
                if d["changed"] or d["missing"]:
                    choice = t.enter_drift(d)
                if choice == "f":
                    from suijin.server.box.cli import cmd_refresh

                    t.message("refresh", ["syncing + installing …"])
                    cmd_refresh([])
                if choice in ("f", "j"):
                    with contextlib.suppress(Exception):
                        curses.endwin()
                    from suijin.modules.platform.lib.config_loader import load_config

                    action = str((load_config() or {}).get("container", {}).get("enter_action") or "suijin")
                    bx.box_enter_interactive(action=action)
                    return 0
            elif k == "r":
                from suijin.server.box.cli import cmd_refresh

                _cmd_output_screen(t, "refresh", cmd_refresh)
            elif k == "s":
                from suijin.server.box.cli import cmd_start, cmd_stop

                _cmd_output_screen(
                    t, "start" if info["running"] is False else "stop", cmd_start if not info["running"] else cmd_stop
                )
            elif k == "d":
                choice = t.confirm_destroy(info)
                if choice in ("archive", "destroy"):
                    from suijin.server.box.cli import archive_box

                    if choice == "archive":
                        t.message("archive", ["streaming engagements to ~/suijn-box-archive-<date>.tar.gz …"])
                        archive_box()
                    bx.box_delete()
            elif k == "t":
                _tools_screen(t)
            elif k == "g":
                t.message(
                    "data",
                    [
                        f"engagements live in the box at {bx.BOX_WS}/engagements",
                        f"{info['engagements']} engagements · {info['size']}",
                        "",
                        "they survive stop; destroy archives them (unless skipped).",
                    ],
                )
            elif k == "x":
                t.message(
                    "settings",
                    [
                        "engine, enter action and the tool list are host-level:",
                        "edit config.json → container {engine, enter_action, tools}",
                        "",
                        "the box has no settings of its own — by design.",
                    ],
                )
        return 0

    return curses.wrapper(_main) or 0


def _run_build(t: Tui) -> None:
    """Drive provision.build with the live screen."""
    import threading

    from suijin.server.box import provision

    state = {"stages": {}, "done": 0, "total": 0, "current": ""}
    tail: list[str] = []
    stop = threading.Event()
    paused = threading.Event()

    def log(m):
        tail.append(str(m))
        tail[:] = tail[-120:]

    def progress(done, total, name, ok, note):
        # ok=None means IN FLIGHT (the tool started, not finished): the
        # current-tool line shows it with a live elapsed clock so the
        # screen visibly ticks (>=3 changes/sec) through long installs
        cur = name if ok is None else ("" if ok else (f"{name} — {note}" if note else name))
        if cur != state["current"]:
            state["started_at"] = time.time()
        state["done"], state["total"], state["current"] = done, total, cur
        if ok is False:
            tail.append(f"x  {name} — {note}")

    def stage_done(name, ok):
        # provision.build fires this as each stage COMPLETES — the rows
        # recolor live (done=green, doing=white, pending=dim)
        state["stages"][name] = {"ok": ok, "at": ""}

    def worker():
        summary = provision.build(log=log, progress=progress, cancel=paused.is_set, stage_cb=stage_done)
        state["summary"] = summary
        stop.set()

    th = threading.Thread(target=worker, daemon=True)
    th.start()

    t.s.nodelay(True)
    try:
        while not stop.is_set():
            state["tick"] = time.time()
            t.build_screen(state, tail)
            k = _get_key(t.s)
            if k == "p":
                paused.set()
                t.s.nodelay(False)
                t.message(
                    "paused", ["finishing the current tool, then pausing.", "re-run `suijin box build` to continue."]
                )
                t.s.nodelay(True)
                stop.set()
            elif k == "esc":
                stop.set()
            time.sleep(0.15)
    finally:
        t.s.nodelay(False)
    summary = state.get("summary") or {}
    t.message(
        "box ready" if summary.get("ok") else "build incomplete",
        [
            f"tools {summary.get('tools_ok', 0)}/{summary.get('tools_total', 0)}",
            "enter with `suijin box enter`",
        ]
        if summary.get("ok")
        else ["re-run `suijin box build` — finished stages resume."],
    )


def _cmd_output_screen(t: Tui, title: str, fn) -> None:
    import contextlib as ctx
    import io

    buf = io.StringIO()

    def pr(m=""):
        print(m, file=buf, flush=True)

    with ctx.suppress(Exception):
        import builtins

        real = builtins.print
        builtins.print = pr
        try:
            fn([])
        finally:
            builtins.print = real
    t.message(title, (buf.getvalue() or "done").splitlines()[:8])


def _tools_screen(t: Tui) -> None:
    from suijin.server.box import engine as bx
    from suijin.server.box.sync import tool_list

    tools = tool_list()
    rows = []
    with contextlib.suppress(Exception):
        from suijin.server.box.provision import presence_script

        rc, out = bx.run_in_box(presence_script(tools), timeout=120)
        rows = [ln.split() for ln in out.splitlines() if len(ln.split()) == 2]
    sel = 0
    while True:
        t.frame("tools")
        t.line(1, 2, f"{sum(1 for _, s in rows if s == 'ok')}/{len(rows)} present", t.cb)
        y = 3
        for i, (name, st) in enumerate(rows[: t.rows - 8]):
            mark = ">" if i == sel else " "
            attr = curses.A_REVERSE if i == sel else 0
            t.line(y, 2, mark, attr)
            if st == "ok":
                t.status(y, 4, "ok", name)
            else:
                t.status(y, 4, "!", name, "missing")
            y += 1
        t.cta([("i", "install selected"), ("a", "install missing"), ("esc", "back")], row=t.rows - 2)
        t.s.refresh()
        k = _get_key(t.s)
        if k == "up":
            sel = max(0, sel - 1)
        elif k == "down":
            sel = min(len(rows) - 1, sel + 1)
        elif k == "a":
            from suijin.server.box.provision import stage_tools

            t.message("install", ["installing missing tools — this runs in the box,", "resumable, idempotent."])
            stage_tools(log=lambda m: None)
            return
        elif k == "i":
            from suijin.server.box.provision import _tool_apt_line

            with contextlib.suppress(Exception):
                name = rows[sel][0]
                bx.run_in_box(_tool_apt_line(name), timeout=2400)
            return
        elif k in ("esc", "q", "t"):
            return
