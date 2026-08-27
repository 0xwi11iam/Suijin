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

import contextlib
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

    def __init__(self, run_box, ui, on_pause=None, modes=MODES):
        self._run_box = run_box  # dispatch target (slash commands + guidance)
        self._ui = ui  # set_input / set_mode sinks
        self._on_pause = on_pause  # double-ESC: pause the agent
        self._modes = tuple(modes)
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
        except termios.error:
            return False
        import atexit

        self._saved_attrs = attrs
        self._termios_saved = True
        atexit.register(self._restore)
        self._thread = threading.Thread(target=self._pump, name="red-input", daemon=True)
        self._thread.start()
        self._ui.set_mode(self._mode)
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
        """One keystroke -> (buffer, action). Actions: 'line', 'tab', None."""
        if key in ("\r", "\n"):
            return "", "line"
        if key in ("\x7f", "\x08"):
            return buf[:-1], None
        if key == "\x15":  # ctrl-u
            return "", None
        if key == "\t":  # Tab cycles the mode
            return buf, "tab"
        if key.isprintable():
            return (buf + key)[:120], None
        return buf, None

    # ── pump ──────────────────────────────────────────────────────────

    def _pump(self) -> None:
        fd = sys.stdin.fileno()
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
                b = sys.stdin.read(1)
            except (OSError, ValueError):
                return
            if not b:
                return  # EOF
            if b == "\x1b":
                # disambiguate: arrow/page sequences start ESC [ / ESC O;
                # a LONE second ESC within 0.6s is the pause chord
                if self._sequence(fd):
                    continue  # swallowed, never lands in the buffer
                self._esc_chord()
                continue
            buf, action = self.apply_key(buf, b)
            if action == "line":
                line, buf = buf, ""
                self._ui.set_input(buf)
                if line.strip():
                    with contextlib.suppress(Exception):
                        self._dispatch(line)
                continue
            if action == "tab":
                self._mode = next_mode(self._mode)
                self._ui.set_mode(self._mode)
                self._ui.set_input(buf)
                continue
            self._ui.set_input(buf)

    def _esc_chord(self) -> None:
        """A lone ESC arrived: within 0.6s of the previous one, PAUSE the
        agent (the Ctrl+C replacement). Single ESCs just register."""
        now = time.monotonic()
        if now - self._last_esc <= 0.6:
            self._last_esc = 0.0
            if self._on_pause:
                with contextlib.suppress(Exception):
                    self._on_pause()
        else:
            self._last_esc = now

    @staticmethod
    def _sequence(fd: int) -> bool:
        """True when the ESC begins an escape sequence (consume it whole)."""
        try:
            r, _, _ = select.select([fd], [], [], 0.03)
        except (OSError, ValueError):
            return False
        if not r:
            return False
        b2 = sys.stdin.read(1)
        if b2 in ("[", "O"):
            # consume until a final byte of the CSI/SS3 sequence
            deadline = time.monotonic() + 0.05
            while time.monotonic() < deadline:
                try:
                    r, _, _ = select.select([fd], [], [], 0.02)
                except (OSError, ValueError):
                    return True
                if not r:
                    break
                f = sys.stdin.read(1)
                if f and f.isalpha():
                    break
            return True
        if b2 == "\x1b":  # second escape arriving instantly — push back: treat as none
            # rare timing: pause chord handled by caller's window; drop both
            return False
        return False  # lone ESC — not a sequence

    def _dispatch(self, line: str) -> None:
        if line.startswith("/"):
            self._run_box.dispatch(line)
            return
        tag = f"[{self._mode.upper()}] "
        self._run_box.dispatch(tag + line)
