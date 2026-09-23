"""Red console input box — the always-at-the-bottom operator prompt.

TTY mode (the live engagement):
  - the box renders in the engagement strip (see console_ui) with the
    mode badge on the left (RECON / EXPLOIT / REPORT) and the thinking
    spinner left of that; typing appears live with a block cursor
  - Tab cycles the mode — the mode tags the prompt you give (plain lines
    dispatch as mode-tagged guidance; slash commands work in any mode)
  - ESC ESC (two escapes within 0.6s) PAUSES the agent — no more Ctrl+C
    gymnastics; arrows/page keys are swallowed, never land in the buffer
  - Enter dispatches through the RunBox (same handlers as always)

Non-TTY (piped, CI, docker): falls back to the RunBox line reader — the
keystroke layer never starts. cbreak keeps ISIG on, so Ctrl+C still
signals as a backup; termios is restored on stop/atexit.
"""

from __future__ import annotations

import codecs
import contextlib
import os
import select
import sys
import threading
import time

MODES = ("recon", "exploit", "report")


def next_mode(m: str) -> str:
    try:
        return MODES[(MODES.index(m) + 1) % len(MODES)]
    except ValueError:
        return MODES[0]


class RedInputReader:
    """cbreak keystroke pump owning stdin (TTY only)."""

    def __init__(self, run_box, ui, on_pause=None, modes=MODES, on_guidance=None, on_pause_line=None):
        self._run_box = run_box  # dispatch target (slash commands + guidance)
        self._ui = ui  # set_input / set_mode sinks
        self._on_pause = on_pause  # double-ESC: pause the agent INSTANTLY
        self._on_guidance = on_guidance  # plain line -> injected into the graph NOW
        self._on_pause_line = on_pause_line  # pause session: reader-side command consumer
        self._ask_queue = None  # set while an ask_operator waits: plain lines ARE the answer
        self._pause_queue = None  # set while the pause console owns input: lines go HERE raw
        self._armed_queue = None  # pre-registered by the engagement: ESC ESC activates it NOW
        self._modes = tuple(modes)
        # RAW byte reads + incremental decode: the text layer BUFFERS (a
        # one-write ESC ESC landed entirely in Python's buffer, select on
        # the fd never saw the second byte, the chord never fired)
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._mode = self._modes[0]
        self._stop = threading.Event()
        self._paused_out = threading.Event()  # paused: pump parked (ask/pause consoles own stdin)
        self._thread: threading.Thread | None = None
        self._saved_attrs = None
        self._termios_saved = False
        self._last_esc = 0.0

    # ── lifecycle ────────────────────────────────────────────────────

    def start(self) -> bool:
        """Enter cbreak + pump. False when stdin isn't a TTY (caller keeps
        the RunBox line reader)."""
        import termios
        import tty

        try:
            fd = sys.stdin.fileno()
        except (AttributeError, ValueError, OSError):
            return False
        try:
            attrs = termios.tcgetattr(fd)
        except termios.error:
            return False
        try:
            tty.setcbreak(fd)  # ISIG stays ON: ^C still signals (backup)
        except termios.error as _e:
            if os.environ.get("SUIJIN_DRIVE_DEBUG"):
                with contextlib.suppress(Exception), open("/tmp/rig_reader_debug.log", "a") as _dbg:
                    _dbg.write(f"setcbreak failed: {_e}\n")
            return False
        import atexit

        self._saved_attrs = attrs
        self._termios_saved = True
        atexit.register(self._restore)
        self._thread = threading.Thread(target=self._pump, name="red-input", daemon=True)
        self._thread.start()
        self._ui.set_mode(self._mode)
        if os.environ.get("SUIJIN_DRIVE_DEBUG"):
            with contextlib.suppress(Exception), open("/tmp/rig_reader_debug.log", "a") as _dbg:
                _dbg.write(f"reader started fd={fd}\n")
        return True

    def _restore(self) -> None:
        import termios

        if self._termios_saved:
            with contextlib.suppress(Exception):
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._saved_attrs)
            self._termios_saved = False

    def stop(self) -> None:
        self._stop.set()
        self._restore()
        self._ui.set_input(None)

    def suspend(self) -> None:
        """A pause/ask console owns stdin: park the pump, restore cooked mode."""
        self._paused_out.set()
        self._restore()

    def resume(self) -> None:
        import termios
        import tty

        if self._stop.is_set():
            return
        self._paused_out.clear()
        with contextlib.suppress(termios.error):
            tty.setcbreak(sys.stdin.fileno())
            self._termios_saved = True

    @property
    def mode(self) -> str:
        return self._mode

    # ── editing (pure, unit-testable) ─────────────────────────────────

    @staticmethod
    def apply_key(buf: str, key: str) -> tuple[str, str | None]:
        """One keystroke -> (buffer, action). Actions: 'line', 'tab', None.
        On 'line' the buffer is PRESERVED — the caller consumes and clears
        it (the old contract returned "" and silently ate every prompt)."""
        if key in ("\r", "\n"):
            return buf, "line"
        if key in ("\x7f", "\x08"):
            return buf[:-1], None
        if key == "\x15":  # ctrl-u
            return "", None
        if key == "\x05":  # Ctrl+E — toggle reasoning mode (always focused)
            return buf, "effort"
        if key == "i" and buf == "\x1b":  # Alt/Option+I — model intelligence
            return "", "intel"
        if key in ("e", "E") and buf == "\x1b":  # Alt/Option+E — reasoning toggle
            return "", "effort"
        if key == "\t":  # Tab cycles the mode
            return buf, "tab"
        if key.isprintable():
            return buf + key, None  # no length cap — the box flexes with the buffer
        return buf, None

    # ── pump ──────────────────────────────────────────────────────────

    def _pump(self) -> None:
        fd = sys.stdin.fileno()
        try:
            self._pump_loop(fd)
        except BaseException:  # the reader must never die silently
            import traceback

            try:
                with open("/tmp/suijin_reader_crash.log", "a") as fh:
                    fh.write(traceback.format_exc() + "\n")
            except OSError:
                pass
            raise

    def _pump_loop(self, fd: int) -> None:
        buf = ""
        while not self._stop.is_set():
            if self._paused_out.is_set():
                time.sleep(0.1)  # someone else owns stdin — never race it
                continue
            try:
                r, _, _ = select.select([fd], [], [], 0.15)
            except (OSError, ValueError):
                return
            if not r:
                continue
            try:
                raw = os.read(fd, 1)
            except (OSError, ValueError):
                return
            if not raw:
                return  # EOF
            b = self._decoder.decode(raw)  # select-consistent: no text buffering
            if not b:
                continue  # mid multi-byte char — wait for the rest
            try:
                buf = self._handle_key(fd, b, buf)
            except Exception:  # noqa: BLE001 — the reader thread IS the app:
                # one bad key must never kill it (the "loop crashed" class).
                # KI/Cancelled pass through (pause path owns those).
                with contextlib.suppress(Exception):
                    import traceback as _tb

                    with open("/tmp/suijin_reader_crash.log", "a") as _fh:
                        _fh.write(f"key {b!r}: {_tb.format_exc()}\n")
                with contextlib.suppress(Exception):
                    self._ui.set_input(buf)

    def _handle_key(self, fd: int, b: str, buf: str) -> str:
        """One decoded key → the updated buffer. Every branch RETURNS; the
        caller's recovery net is the only catcher (the old inline body
        could unwind the thread on any UI-callback fault — dead input for
        the rest of the engagement)."""
        if os.environ.get("SUIJIN_DRIVE_DEBUG"):
            with contextlib.suppress(Exception), open("/tmp/rig_keys.log", "a") as _kl:
                _kl.write(f"{b!r}\n")
        if b == "\x1b":
            # disambiguate: arrow/page sequences start ESC [ / ESC O; the
            # pause chord is a lone or doubled ESC
            seq = self._sequence(fd)
            if seq is True:
                return buf  # page keys etc — swallowed, never land in the buffer
            if seq in ("up", "down"):
                # the instinct every user has: arrows navigate the
                # suggestion list
                with contextlib.suppress(Exception):
                    self._move_suggestion(-1 if seq == "up" else 1)
                return buf
            if seq == "alt-i":
                # Alt/Option+I — cycle model intelligence
                self._cycle_intelligence()
                return buf
            if seq == "alt-e":
                # Alt/Option+E — reasoning on/off (macOS Option is ESC-prefixed)
                self._toggle_reasoning()
                return buf
            if seq == "esc":
                self._fire_chord()  # zero-gap double ESC
                return buf
            self._esc_chord()  # lone ESC — the chord window opens/updates
            return buf
        buf, action = self.apply_key(buf, b)
        if action == "line":
            line, buf = buf, ""
            self._ui.set_input(None)  # clear FIRST — no residual artifact
            if line.strip():
                with contextlib.suppress(Exception):
                    self._dispatch(line)
            return buf
        if action == "tab":
            self._mode = next_mode(self._mode)
            self._ui.set_mode(self._mode)
            self._ui.set_input(buf)
            return buf
        if action == "intel":
            self._cycle_intelligence()
            self._ui.set_input(buf)  # keep typing intact
            return buf
        if action == "effort":
            self._toggle_reasoning()
            self._ui.set_input(buf)
            return buf
        self._ui.set_input(buf)
        return buf

    def _suggestions_active(self) -> bool:
        """The suggestion bar is showing: the buffer starts with '/'."""
        from suijin.modules.redteam.lib.red.console_ui import UI_STATE

        return str(UI_STATE.get("input_buf") or "").startswith("/")

    def _move_suggestion(self, delta: int) -> None:
        """Arrow up/down: cycle the suggestion selection."""
        from suijin.modules.redteam.lib.red.console_ui import UI_STATE

        matches = self._ui._filtered_suggestions() if hasattr(self._ui, "_filtered_suggestions") else []
        if not matches:
            return
        cur = UI_STATE.get("suggest_sel")
        cur = 0 if cur is None else int(cur)
        UI_STATE["suggest_sel"] = (cur + delta) % min(len(matches), 8)
        self._ui.set_input(UI_STATE.get("input_buf") or "")

    def _operator_run(self, cmd: str) -> None:
        """<run>cmd</run>: execute in the OPERATOR's shell (subprocess,
        not the agent's execute_terminal). Output prints above the strip
        in a bordered panel. Timeout 30s; the agent is never touched."""
        import subprocess

        from rich.panel import Panel as _Panel
        from rich.text import Text as _Text

        try:
            r = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=os.path.expanduser("~"),
            )
            out = (r.stdout or "") + (r.stderr or "")
            body = _Text.assemble(
                ("$ ", "bold green"),
                (cmd, "bold white"),
                ("\n", ""),
                (out[:2000] if out.strip() else "(no output)", "dim"),
            )
            if r.returncode != 0:
                body.append(f"\n[rc={r.returncode}]", style="bold red")
            self._ui.console.print(_Panel(body, title=" run ", title_align="left", border_style="cyan", padding=(0, 1)))
        except subprocess.TimeoutExpired:
            self._ui.console.print(f"[red]  \u25b8 run timeout (30s): {cmd[:80]}[/red]")
        except Exception as e:  # noqa: BLE001
            self._ui.console.print(f"[red]  \u25b8 run failed: {e}[/red]")

    def _toggle_reasoning(self) -> None:
        """Ctrl+E: reasoning ON <-> OFF. OFF stores the current tier in
        _last_effort and drops to 'low' (thinking disabled at the
        provider); ON restores the stored tier (or 'high' when nothing
        was stored). Applies on the NEXT LLM call — between thoughts."""
        from suijin.modules.redteam.lib.red.console_ui import UI_STATE

        cur = str(UI_STATE.get("intelligence", "max"))
        if cur == "low":
            UI_STATE["intelligence"] = str(UI_STATE.pop("_last_effort", "") or "high")
        else:
            UI_STATE["_last_effort"] = cur
            UI_STATE["intelligence"] = "low"
        self._ui.set_mode(self._mode)  # tick the strip so the tier renders

    def _cycle_intelligence(self) -> None:
        """Alt/Option+I: cycle model intelligence — applies on the NEXT
        LLM call (between thoughts, per the operator contract)."""
        from suijin.modules.redteam.lib.red.console_ui import UI_STATE

        tiers = self._ui._supported_tiers()
        cur = str(UI_STATE.get("intelligence", "max"))
        if cur not in tiers:
            cur = tiers[0] if tiers else "max"
        UI_STATE["intelligence"] = tiers[(tiers.index(cur) + 1) % len(tiers)] if tiers else "max"
        self._ui.set_mode(self._mode)  # tick the strip so the tier renders

    def _fire_chord(self) -> None:
        """The pause chord fired: on_pause runs (instant visual + session),
        routing switches to the armed queue immediately."""
        self._last_esc = 0.0
        if self._on_pause:
            with contextlib.suppress(Exception):
                self._on_pause()
        if getattr(self, "_armed_queue", None) is not None:
            self._pause_queue = self._armed_queue

    def _esc_chord(self) -> bool:
        """A lone ESC arrived: within 0.6s of the previous one, PAUSE the
        agent (the Ctrl+C replacement). Single ESCs just register.
        Returns True when the pause fired."""
        now = time.monotonic()
        if now - self._last_esc <= 0.6:
            self._fire_chord()
            return True
        self._last_esc = now
        return False

    @staticmethod
    def _sequence(fd: int) -> bool:
        """True when the ESC begins an escape sequence (consume it whole)."""
        try:
            r, _, _ = select.select([fd], [], [], 0.03)
        except (OSError, ValueError):
            return False
        if not r:
            return False
        b2 = os.read(fd, 1)
        if b2 == b"i":
            return "alt-i"  # Alt/Option+I (macOS Option sends ESC-prefixed keys)
        if b2 in (b"e", b"E"):
            return "alt-e"  # Alt/Option+E — reasoning toggle (macOS-friendly)
        if b2 in (b"[", b"O"):
            # consume until a final byte of the CSI/SS3 sequence
            deadline = time.monotonic() + 0.05
            collected = b2
            while time.monotonic() < deadline:
                try:
                    r, _, _ = select.select([fd], [], [], 0.02)
                except (OSError, ValueError):
                    return True
                if not r:
                    break
                f = os.read(fd, 1)
                collected += f or b""
                if f and f.isalpha():
                    break
            if collected in (b"[A", b"OA"):
                return "up"
            if collected in (b"[B", b"OB"):
                return "down"
            return True
        if b2 == b"\x1b":
            return "esc"  # second ESC back-to-back: the CHORD, zero-gap — caller fires
        return False  # lone ESC — not a sequence

    # ── pause mode: the omnipresent box feeds the pause console ──────

    def arm_pause(self, queue) -> None:
        """Pre-register the pause queue. The INSTANT ESC ESC fires, the
        reader routes lines there itself — no waiting for the main thread
        to land (the graph may take seconds to unwind; the box must not)."""
        self._armed_queue = queue

    def begin_ask(self, queue) -> None:
        """An ask_operator is waiting: every entered line routes RAW into
        the queue as the ANSWER (no mode tag, no command parsing) — the
        old console.input fallback fought the cbreak reader and typing
        died. The box stays the one and only inputter."""
        self._ask_queue = queue

    def end_ask(self) -> None:
        self._ask_queue = None

    def begin_pause(self, queue=None) -> None:
        """The engagement paused: every entered line routes RAW into the
        queue (the pause console consumes it) — the box never yields to a
        legacy prompt; it stays the one and only inputter."""
        self._pause_queue = queue if queue is not None else self._armed_queue

    def end_pause(self) -> None:
        self._pause_queue = None
        # the armed queue STAYS armed: the next ESC ESC re-routes instantly

    def _dispatch(self, line: str) -> None:
        if os.environ.get("SUIJIN_DRIVE_DEBUG"):
            with contextlib.suppress(Exception), open("/tmp/rig_keys.log", "a") as _kl:
                _kl.write(
                    f"dispatch: {line!r} pause_line={getattr(self, '_on_pause_line', None) is not None} "
                    f"guidance={getattr(self, '_on_guidance', None) is not None} "
                    f"pause_q={getattr(self, '_pause_queue', None) is not None} "
                    f"ask_q={getattr(self, '_ask_queue', None) is not None}"
                )
        if getattr(self, "_ask_queue", None) is not None:
            # an ask_operator consumes plain lines as the ANSWER, raw
            with contextlib.suppress(Exception):
                self._ask_queue.put(line)
            return
        if getattr(self, "_on_pause_line", None) is not None:
            # a pause session OWNS input: the reader consumes the line
            # itself — commands answer instantly, no main-thread dependency
            try:
                self._on_pause_line(line)
            except BaseException:
                import traceback

                with contextlib.suppress(Exception), open("/tmp/suijin_reader_crash.log", "a") as fh:
                    fh.write("pause_line: " + traceback.format_exc() + "\n")
            return
        if getattr(self, "_pause_queue", None) is not None:
            with contextlib.suppress(Exception):
                self._pause_queue.put(line)
            return
        if line.startswith("/"):
            self._run_box.dispatch(line)
            return
        # <run>cmd</run> — the operator's inline shell (NOT the agent's)
        import re as _re_mod

        _runs = _re_mod.findall(r"<run>(.+?)</run>", line)
        if _runs:
            for _cmd in _runs:
                self._operator_run(_cmd)
            return
        # a pending ask_operator consumes plain lines as the ANSWER (raw)
        if getattr(self._run_box, "_ask_mode", False):
            self._run_box.dispatch(line)
            return
        if getattr(self, "_on_guidance", None) is not None:
            # INSTANT: injected into the graph state now — no waiting for a
            # pause or turn boundary; the echo stays in the UI, not the queue
            with contextlib.suppress(Exception):
                self._on_guidance(line)
            return
        tag = f"[{self._mode.upper()}] "
        self._run_box.dispatch(tag + line)
