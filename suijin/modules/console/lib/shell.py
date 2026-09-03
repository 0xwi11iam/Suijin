"""shell — the Textual UI shell: boot flow, mode selector, entry pages.

Owns everything from `suijin` launch to engagement start. The dragon
rides the left pane on every page (banner.styled_lines — cyan base,
red bands, white eye); the right pane is the current page. On LAUNCH
the app EXITS first, then the existing engagement launch code runs —
the runtimes' one-Live/one-stdin invariants are untouched.

No-breakage contract: every page has Back (Esc + button); errors render
inline on the same page; operator tools suspend the app around their
subprocess so terminal control is clean.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Center, Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, Static

# platform imports stay function-local (kernel purity boundary gate)

# ── block headers ────────────────────────────────────────────────────


def _header(text: str, color: str = "bold white") -> Text:
    from suijin.modules.platform.lib import blockfont

    rows = blockfont.render(text)
    return Text("\n".join(r.plain for r in rows), style=color)


# ── the dragon widget ────────────────────────────────────────────────

_STYLE_MAP = {"cyan": "cyan", "red": "rgb(255,64,64)", "eye": "bold white"}


class DragonWidget(Static):
    """The persistent left-pane dragon — every page composes one."""

    def on_mount(self) -> None:
        from suijin.modules.platform.lib.banner import styled_lines

        t = Text()
        for segs in styled_lines():
            for text, style in segs:
                t.append(text, style=_STYLE_MAP.get(style, "cyan"))
            t.append("\n")
        self.update(t)


# ── result contract ──────────────────────────────────────────────────


class ShellResult:
    """What the operator chose. `kind`: quit | red | blue | blue_lab."""

    def __init__(self, kind: str, **kw):
        self.kind = kind
        self.__dict__.update(kw)


# ── page base ────────────────────────────────────────────────────────


class ShellPage(Screen):
    """Right-pane page: header + scrollable content. Esc = Back."""

    BINDINGS = [("escape", "back", "Back")]
    title_text = ""
    title_color = "bold white"

    def compose(self) -> ComposeResult:
        yield DragonWidget()
        with Container(id="page"), VerticalScroll(id="content"):
            if self.title_text:
                yield Static(_header(self.title_text, self.title_color), id="pagetitle")
            yield from self.body()
        yield Footer()

    def body(self):  # pragma: no cover - overridden
        return

    def action_back(self) -> None:
        self.app.pop_screen()


# ── P0 Welcome ───────────────────────────────────────────────────────


def _version_rows() -> Text:
    """v6.6.1 in block digits: v green, digits alternate red/blue."""
    try:
        v = json.loads((Path(__file__).resolve().parents[3] / "version.json").read_text())["version"]
    except Exception:  # noqa: BLE001
        v = "0.0.0"
    colors = {"v": "green"}

    def style_of(ch, i):
        if ch == "v":
            return colors["v"]
        if ch.isdigit():
            return "red" if i % 2 == 0 else "blue"
        return "dim"

    from suijin.modules.platform.lib import blockfont

    rows = blockfont.render(f"v{v}", style_of=style_of)
    return Text("\n".join(r.plain for r in rows))


class WelcomeScreen(ShellPage):
    title_text = ""
    CSS = """
    #wordmark { margin: 1 0; }
    #versionart { margin: 1 0; }
    """

    def body(self):
        from suijin.modules.platform.lib.banner import WORDMARK

        yield Static(WORDMARK, id="wordmark", markup=False)
        yield Static(_version_rows(), id="versionart")
        yield Center(Button("▸ Press Enter to start", id="start", variant="primary"))

    BINDINGS = [("enter", "start", "Start"), ("escape", "quit", "Quit")]

    def action_start(self) -> None:
        self.app.push_screen(BootScreen())

    def action_quit(self) -> None:
        self.app.exit(ShellResult("quit"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            self.action_start()


# ── P1 Boot (spinner + INITIALIZING...) ──────────────────────────────

_SPIN_FRAMES = [
    "█░░\n░█░\n░░█",
    "░█░\n░█░\n░█░",
    "░░█\n░█░\n█░░",
    "█░░\n░█░\n░░█",
]


class BootScreen(Screen):
    CSS = """
    #bootbox { width: 1fr; align: center middle; }
    #spinner { width: auto; text-style: bold; color: rgb(255,64,64); }
    #boottext { margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        with Container(id="bootbox"), Horizontal(id="bootrow"):
            yield Static(_SPIN_FRAMES[0], id="spinner")
            yield Static(_header("INITIALIZING", "bold cyan"), id="boottext")
        yield DragonWidget()

    def on_mount(self) -> None:
        self._frame = 0
        self._spin = self.set_interval(0.2, self._tick)  # 5fps
        self.set_timer(random.uniform(1.0, 3.0), self._advance)

    def _tick(self) -> None:
        import contextlib

        self._frame = (self._frame + 1) % len(_SPIN_FRAMES)
        with contextlib.suppress(Exception):  # never break the boot
            self.query_one("#spinner", Static).update(_SPIN_FRAMES[self._frame])

    def _advance(self) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            self._spin.stop()
        self.app.pop_screen()
        self.app.push_screen(SelectorScreen())


# ── P2 Mode Selector ────────────────────────────────────────────────

_MODES = [
    ("red", "RED TEAM", "autonomous agent", "#ff5555"),
    ("blue", "BLUE TEAM", "active defense", "#58a6ff"),
    ("settings", "SETTINGS", "provider, posture, caps", "yellow"),
    ("operator", "OPERATOR TOOLS", "scope, approvals, battle…", "#e6b47c"),
    ("exit", "EXIT", "", "grey"),
]


class SelectorScreen(ShellPage):
    title_text = "MODE SELECTOR"
    title_color = "bold white"
    CSS = """
    ModeButton { width: 100%; height: 3; content-align: center middle; }
    """

    def body(self):
        for key, label, sub, color in _MODES:
            b = Button(f"{label}  ·  {sub}" if sub else label, id=f"mode-{key}", classes="modebtn")
            b.styles.color = color
            yield b

    def on_button_pressed(self, event: Button.Pressed) -> None:
        key = event.button.id.removeprefix("mode-")
        if key == "red":
            self.app.push_screen(RedEntryScreen())
        elif key == "blue":
            self.app.push_screen(BlueEntryScreen())
        elif key == "settings":
            self.app.push_screen(SettingsScreen())
        elif key == "operator":
            self.app.push_screen(OperatorScreen())
        elif key == "exit":
            self.app.exit(ShellResult("quit"))


# ── P3 Red flow ──────────────────────────────────────────────────────


class RedEntryScreen(ShellPage):
    title_text = "RED TEAM"
    title_color = "bold #ff5555"

    def body(self):
        yield Button("▸ Type the objective manually", id="red-type", classes="modebtn")
        yield Button("▸ Upload file (.txt / .md / .rtf)", id="red-upload", classes="modebtn")
        yield Button("◂ Back", id="back", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "red-type":
            self.app.push_screen(ObjectiveInputScreen())
        elif event.button.id == "red-upload":
            self.app.push_screen(UploadFileScreen())
        elif event.button.id == "back":
            self.action_back()


class ObjectiveInputScreen(ShellPage):
    title_text = "OBJECTIVE"

    def body(self):
        yield Label("Target / objective:")
        yield Input(placeholder="e.g. Assess https://target.example.com — full engagement order", id="obj")
        yield Button("Continue ▸", id="continue", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "continue":
            val = self.query_one("#obj", Input).value.strip()
            if not val:
                self.notify("Objective is empty — type it or go Back", severity="error")
                return
            self.app.push_screen(RedPreviewScreen(val))


class UploadFileScreen(ShellPage):
    title_text = "UPLOAD FILE"

    def body(self):
        yield Label("Drag the file into this field (terminals paste the path) or type it:")
        yield Input(placeholder="/path/to/objective.txt", id="path")
        yield Label("", id="loadmsg")
        yield Button("Continue ▸", id="continue", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "continue":
            raw = self.query_one("#path", Input).value.strip()
            from suijin.modules.redteam.lib.red.session_control import load_objective_from_file

            msg = load_objective_from_file(raw)  # returns the objective text or an error string
            msg_l = str(msg).lower()
            if msg_l.startswith(("file not found", "not a file", "read error", "file is empty", "error")):
                self.query_one("#loadmsg", Label).update(f"[red]{msg}[/red]")
                return
            self.app.push_screen(RedPreviewScreen(str(msg)))


class RedPreviewScreen(ShellPage):
    title_text = "LAUNCH"

    def __init__(self, objective: str):
        super().__init__()
        self.objective = objective

    def body(self):
        yield Label(f"Objective ({len(self.objective)} chars):")
        yield Static(self.objective[:500] + ("…" if len(self.objective) > 500 else ""), id="preview")
        yield Label(self._auth_line(), id="authline")
        yield Button("▸ LAUNCH ENGAGEMENT", id="launch", variant="error")

    def _auth_line(self) -> str:
        try:
            from suijin.modules.ops.lib.authorizations import authorization_line

            line = authorization_line(self.objective)
            return f"[green]{line}[/green]" if line else (
                "[dim]No authorization on file — `suijin authorize <target>` puts it on record[/dim]"
            )
        except Exception:  # noqa: BLE001
            return "[dim]authorization ledger unavailable[/dim]"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "launch":
            self.app.exit(ShellResult("red", objective=self.objective))


# ── P4 Blue flow ────────────────────────────────────────────────────


class BlueEntryScreen(ShellPage):
    title_text = "BLUE TEAM"
    title_color = "bold #58a6ff"

    def body(self):
        yield Button("▸ Defend a codebase (path + port)", id="blue-path", classes="modebtn")
        yield Button("▸ Use a built-in lab", id="blue-lab", classes="modebtn")
        yield Button("◂ Back", id="back", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "blue-path":
            self.app.push_screen(BluePathScreen())
        elif event.button.id == "blue-lab":
            self.app.push_screen(BlueLabScreen())
        elif event.button.id == "back":
            self.action_back()


class BluePathScreen(ShellPage):
    title_text = "CODEBASE"

    def body(self):
        yield Label("Path to the codebase:")
        yield Input(placeholder="/path/to/app", id="bpath")
        yield Label("App port:")
        yield Input(placeholder="8000", id="bport")
        yield Label("", id="perr")
        yield Button("▸ LAUNCH DEFENSE", id="launch", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "launch":
            path = self.query_one("#bpath", Input).value.strip()
            port = self.query_one("#bport", Input).value.strip()
            err = self.query_one("#perr", Label)
            if not path or not Path(path).expanduser().is_dir():
                err.update(f"[red]Invalid path: {path or '(empty)'}[/red]")
                return
            if not port.isdigit():
                err.update("[red]Port must be a number[/red]")
                return
            self.app.exit(ShellResult("blue", path=path, port=int(port)))


class BlueLabScreen(ShellPage):
    title_text = "BUILT-IN LABS"

    LABS = [
        ("blue_target", "classic · 25 endpoints · :5906"),
        ("hill_ctf", "four perimeters · rotating vault token"),
    ]

    def body(self):
        for key, desc in self.LABS:
            yield Button(f"{key}  ·  {desc}", id=f"lab-{key}", classes="modebtn")
        yield Button("◂ Back", id="back", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("lab-"):
            self.app.exit(ShellResult("blue_lab", lab=event.button.id.removeprefix("lab-")))
        elif event.button.id == "back":
            self.action_back()


# ── P5 Settings (curses → Textual) ──────────────────────────────────


def _settings_fields() -> list[tuple[str, tuple]]:
    try:
        from suijin.modules.console.lib.tui_settings import ALL_FIELDS

        return list(ALL_FIELDS.items())
    except Exception:  # noqa: BLE001
        return []


class SettingsScreen(ShellPage):
    title_text = "SETTINGS"
    title_color = "bold yellow"

    def body(self):
        for name, spec in _settings_fields():
            kind = spec[0]
            hint = {"choice": f"choice: {', '.join(map(str, spec[1][:4]))}…", "bool": "on / off"}.get(kind, kind)
            yield Button(f"{name}  ·  {hint}", id=f"set-{name}", classes="modebtn")
        yield Button("◂ Back", id="back", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.action_back()
        elif event.button.id and event.button.id.startswith("set-"):
            name = event.button.id.removeprefix("set-")
            fields = dict(_settings_fields())
            if name in fields:
                self.app.push_screen(SettingEditorScreen(name, fields[name]))


class SettingEditorScreen(ShellPage):
    def __init__(self, name: str, spec: tuple):
        super().__init__()
        self.name = name
        self.spec = spec

    def body(self):
        yield Label(f"Editing [bold]{self.name}[/bold] — Esc/Back returns without saving")
        kind = self.spec[0]
        if kind == "choice":
            for opt in self.spec[1]:
                yield Button(str(opt), id=f"opt-{opt}", classes="modebtn")
        elif kind == "bool":
            yield Button("true", id="opt-true", classes="modebtn")
            yield Button("false", id="opt-false", classes="modebtn")
        else:  # text / int / float
            yield Input(placeholder=f"new value ({kind})", id="newval")
            yield Button("Save", id="opt-save", variant="primary")

    def title(self):  # textual Screen.title as property-friendly str
        return f"EDIT {self.name.upper()}"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("opt-"):
            value = bid.removeprefix("opt-")
            self._save(value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "newval":
            self._save(event.value)

    def _save(self, value: str) -> None:
        try:
            cfg_path = Path(__file__).resolve().parents[3] / "config.json"
            cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
            kind = self.spec[0]
            if kind == "int":
                cfg[self.name] = int(value)
            elif kind == "float":
                cfg[self.name] = float(value)
            elif kind == "bool":
                cfg[self.name] = value.lower() == "true"
            else:
                cfg[self.name] = value
            cfg_path.write_text(json.dumps(cfg, indent=4))
            self.notify(f"{self.name} = {cfg[self.name]} saved")
        except Exception as e:  # noqa: BLE001
            self.notify(f"save failed: {e}", severity="error")
        self.action_back()


# ── P6 Operator Tools ───────────────────────────────────────────────


def _operator_tools():
    try:
        from suijin.main import OPERATOR_TOOLS

        return OPERATOR_TOOLS
    except Exception:  # noqa: BLE001
        return []


class OperatorScreen(ShellPage):
    title_text = "OPERATOR TOOLS"
    title_color = "bold #e6b47c"

    def body(self):
        for i, (label, args) in enumerate(_operator_tools(), 1):
            yield Button(f"{label}", id=f"tool-{i}", classes="modebtn")
        yield Button("◂ Back", id="back", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.action_back()
        elif event.button.id and event.button.id.startswith("tool-"):
            idx = int(event.button.id.removeprefix("tool-")) - 1
            tools = _operator_tools()
            if 0 <= idx < len(tools):
                label, args = tools[idx]
                if len(args) >= 2 and args[0] in ("exploit", "load", "dossier"):
                    self.app.push_screen(ToolPromptScreen(label, args))
                else:
                    self._run(args)

    def _run(self, args: list) -> None:
        import subprocess
        import sys

        cmd = [sys.executable, str(Path(__file__).resolve().parents[3] / "modules/console/lib/cli.py"), *args]
        import contextlib

        with self.app.suspend():
            subprocess.run(cmd)
            with contextlib.suppress(EOFError):
                input("\n[dim]Press Enter to return...[/dim] ")


class ToolPromptScreen(ShellPage):
    def __init__(self, label: str, args: list):
        super().__init__()
        self.label = label
        self.args = list(args)

    PROMPTS = {
        "exploit": "Target (IP / hostname / URL):",
        "load": ".sje path (or name from outputs/exports/):",
        "dossier": "Target (IP / hostname / URL):",
    }

    def body(self):
        prompt = self.PROMPTS.get(self.args[0], "Value:")
        yield Label(prompt)
        yield Input(placeholder="…", id="toolval")
        yield Button("Run ▸", id="run", variant="primary")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "toolval":
            self._go(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            self._go(self.query_one("#toolval", Input).value)

    def _go(self, value: str) -> None:
        if not value.strip():
            self.notify("value required", severity="error")
            return
        args = [self.args[0], value.strip(), *self.args[2:]]
        OperatorScreen._run(self, args)
        self.action_back()


# ── the app ──────────────────────────────────────────────────────────


class ShellApp(App):
    """The boot shell. run() returns a ShellResult (or None on quit)."""

    TITLE = "SUIJIN"
    CSS = """
    Screen { layout: horizontal; }
    DragonWidget { width: 46; padding: 0 1; overflow: hidden; }
    #page { width: 1fr; }
    #content { padding: 1 2; }
    #pagetitle { margin-bottom: 1; }
    ModeButton { width: 100%; margin-bottom: 1; }
    #back { margin-top: 1; }
    """
    BINDINGS = [("ctrl+c", "quit_app", "Quit")]

    def __init__(self):
        super().__init__()
        self.result: ShellResult | None = None

    def on_mount(self) -> None:
        self.push_screen(WelcomeScreen())

    def exit(self, result=None, **kw) -> None:  # noqa: D102
        if isinstance(result, ShellResult):
            self.result = result
        super().exit(**kw)

    def action_quit_app(self) -> None:
        self.exit(ShellResult("quit"))


def run_shell():
    """Run the shell; returns ShellResult | None."""
    app = ShellApp()
    app.run()
    return app.result
