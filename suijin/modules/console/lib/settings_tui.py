"""Suijin Settings — the Textual TUI.

Replaces the curses settings editor: two panes after the Module Manager
house pattern — grouped field list left, typed editor right (Switch for
bools, Select for choices, Input for strings/numbers with clamp-on-save).
ctrl+S saves, Esc exits without saving; every action is notify()-bounded
(no framework crash screens). Non-TTY falls back to a printed summary.
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Input, ListView, Select, Static, Switch

# The package-level config (suijin/config.json) — resolved from this file's
# location so it works from the dev symlink too.
CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "config.json"
)


def _registry_provider_choices():
    """Registry providers + any custom:<name> entries in config.json."""
    choices = []
    try:
        from suijin.modules.providers.lib.registry import CHINA_KEYS, CLOUD_KEYS, LOCAL_KEYS, WESTERN_KEYS

        choices = CLOUD_KEYS + CHINA_KEYS + WESTERN_KEYS + LOCAL_KEYS
    except Exception:  # noqa: BLE001 — Settings must open even headless
        pass
    try:
        cfg = json.loads(Path(CONFIG_PATH).read_text()) if os.path.isfile(CONFIG_PATH) else {}
        for entry in cfg.get("custom_providers") or []:
            name = str(entry.get("name", "")).strip()
            if name:
                choices.append(f"custom:{name}")
    except Exception:  # noqa: BLE001
        pass
    return choices


# ---- Field definitions (ported semantics: (type, extra[, provider filter])) ----
ALL_FIELDS = OrderedDict(
    [
        # ---- Provider ----
        (
            "provider",
            ("choice", ["deepseek", "huggingface", "gemini", "anthropic", "amd", "zai"] + _registry_provider_choices()),
        ),
        # ---- DeepSeek ----
        ("deepseek_model", ("choice", ["deepseek-chat", "deepseek-reasoner"], ["deepseek"])),
        # ---- Z.ai ----
        # zai_endpoint: "coding" = GLM Coding Plan subscription quota (default;
        # burns plan credits). "paas" = pay-as-you-go per-token USD billing.
        # Same key, but a plan key on paas (or PAYG on coding) returns 403.
        ("zai_endpoint", ("choice", ["coding", "paas"], ["zai"])),
        ("zai_model", ("choice", ["glm-5.3", "glm-5-turbo", "glm-5.1", "glm-4.7", "glm-4.6"], ["zai"])),
        # ---- HuggingFace ----
        ("final_model_id", ("string", None, ["huggingface"])),
        ("sentinel_model_id", ("string", None, ["huggingface"])),
        # ---- Gemini ----
        ("gemini_model", ("choice", ["gemini-2.5-pro", "gemini-2.5-flash"], ["gemini"])),
        # ---- Anthropic ----
        (
            "anthropic_model",
            ("choice", ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"], ["anthropic"]),
        ),
        # ---- Universal fields (all providers) ----
        ("posture", ("choice", ["assertive", "recon"])),
        ("temperature", ("float", (0.0, 2.0))),
        ("max_tokens_per_request", ("int", (1, 128000))),
        ("context_window", ("int", (0, 10_000_000))),
        ("max_iterations", ("int", (1, 1000000))),
        ("supervisor_model_id", ("string",)),
        ("supervisor_interval", ("int", (1, 100))),
        ("librarian_interval", ("int", (1, 1000))),
        # ---- Cost guardrails ----
        ("cost_alert_usd", ("float", (0.0, 1000.0))),
        ("cost_budget_usd", ("float", (0.0, 1000.0))),
        ("cost_hard_cap_usd", ("float", (0.0, 1000.0))),
        # ---- Proxy ----
        ("proxy_url", ("string",)),
        # ---- Operational Modes ----
        ("mode_hitl", ("bool",)),
        ("mode_guardrail", ("bool",)),
        ("mode_deploy_subagent", ("bool",)),
        ("mode_audit_trail", ("bool",)),
        ("subagent_count", ("int", (1, 5))),
        # ---- Workspace & integrations ----
        ("metasploit_rpc_host", ("string",)),
        ("metasploit_rpc_port", ("int", (1, 65535))),
    ]
)

_GROUPS = [
    (
        "Provider",
        [
            "provider",
            "deepseek_model",
            "zai_endpoint",
            "zai_model",
            "final_model_id",
            "sentinel_model_id",
            "gemini_model",
            "anthropic_model",
        ],
    ),
    (
        "Engagement",
        [
            "posture",
            "temperature",
            "max_tokens_per_request",
            "context_window",
            "max_iterations",
            "supervisor_model_id",
            "supervisor_interval",
            "librarian_interval",
        ],
    ),
    ("Cost guardrails", ["cost_alert_usd", "cost_budget_usd", "cost_hard_cap_usd"]),
    ("Proxy", ["proxy_url"]),
    (
        "Operational modes",
        ["mode_hitl", "mode_guardrail", "mode_deploy_subagent", "mode_audit_trail", "subagent_count"],
    ),
    ("Integrations", ["metasploit_rpc_host", "metasploit_rpc_port"]),
]


def load_config() -> dict:
    if not os.path.isfile(CONFIG_PATH):  # is_file: a bind-mount can leave a directory
        return {}
    try:
        cfg = json.loads(Path(CONFIG_PATH).read_text())
        return cfg if isinstance(cfg, dict) else {}
    except Exception:  # noqa: BLE001 — Settings must open on a corrupt config
        return {}


def load_state() -> tuple[dict, str]:
    """(config, status) where status ∈ ok | missing | corrupt — the app
    REFUSES to save over a corrupt file (saving the in-memory near-empty
    dict would wipe the operator's config)."""
    if not os.path.isfile(CONFIG_PATH):
        return {}, "missing"
    try:
        cfg = json.loads(Path(CONFIG_PATH).read_text())
        if not isinstance(cfg, dict):
            raise ValueError("top level is not an object")
        return cfg, "ok"
    except Exception:  # noqa: BLE001
        return {}, "corrupt"


def save_config(config: dict) -> None:
    Path(CONFIG_PATH).write_text(json.dumps(config, indent=4))


def _visible_fields(config: dict) -> "OrderedDict[str, tuple]":
    provider = config.get("provider", "deepseek")
    visible = OrderedDict()
    for key, field_def in ALL_FIELDS.items():
        filters = field_def[2] if len(field_def) > 2 else None
        if filters is not None and provider not in filters:
            continue
        visible[key] = field_def
    return visible


def _eq(a, b) -> bool:
    """Loose echo comparison — '500' == 500 == 500.0 for render-echo checks."""
    try:
        return str(a) == str(b)
    except Exception:  # noqa: BLE001
        return a == b


class SettingsApp(App):
    """Two-pane settings editor — field list left, a SCROLLING FORM of
    typed editors right (one mounted editor per visible field).

    The hard-won design rules (Textual 8.x):
      - never mount/remove editors per selection — it wedges the pump
      - never display-toggle a shared editor — same wedge, different path
      - every editor carries its field key as its `name`; events write
        through THAT key, never the current list highlight (cross-field
        corruption)
      - mount-time value assignments echo as Changed events — suppressed
        by comparing against the value we installed
    """

    TITLE = "SUIJIN — Settings"
    CSS = """
    #body { height: 1fr; }
    #list { width: 40%; border: solid $accent; }
    #form { width: 60%; border: solid $surface; padding: 0 1; }
    .group-label { text-style: bold; color: $accent; height: auto; margin-top: 1; }
    .field-label { height: auto; padding-top: 1; color: $text; }
    .field-hint { height: auto; color: $text-muted; }
    .field-widget { margin: 0 1 1 1; }
    #buttons { height: auto; dock: bottom; padding: 0 1; }
    """
    BINDINGS = [
        ("ctrl+s", "save", "Save + exit"),
        ("q", "save", "Save + exit"),
        ("escape", "cancel", "Exit without saving"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._cfg, self._status = load_state()
        self._cfg.setdefault("provider", "deepseek")
        self._rendered: dict[str, object] = {}  # field -> value installed at mount
        self._editors: dict[str, object] = {}  # field -> its editor widget

    # ── layout ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        from textual.containers import Horizontal as _H
        from textual.widgets import Button, ListView

        yield Header()
        with _H(id="body"):
            yield ListView(id="list")
            yield VerticalScroll(id="form")
        with _H(id="buttons"):
            yield Button("Save (ctrl+s)", id="btn-save", variant="success")
            yield Button("Cancel (id: Esc)", id="btn-cancel", variant="default")
        yield Footer()

    def on_mount(self) -> None:
        self.call_after_refresh(self._rebuild)

    # ── build the two panes ───────────────────────────────────────────

    async def _rebuild(self, keep: str = "") -> None:
        """(Re)build list + form for the current provider. Called at mount
        and on provider swaps — the only mount/remove churn in the app."""
        try:
            from textual.widgets import ListItem as _LI

            lv = self.query_one("#list", ListView)
            form = self.query_one("#form", VerticalScroll)
            visible = _visible_fields(self._cfg)
            lv.clear()
            await form.remove_children()
            self._rendered.clear()
            self._editors.clear()
            sel_idx = None
            first_idx = None
            for title, keys in _GROUPS:
                vis = [k for k in keys if k in visible]
                if not vis:
                    continue
                lv.append(_LI(Static(f"[{title}]", markup=False), disabled=True, name=f"__header__{title}"))
                await form.mount(Static(f" {title}", classes="group-label"))
                for k in vis:
                    val = self._fmt(k, self._cfg.get(k, ""))
                    lv.append(_LI(Static(f"{k}: {val}", markup=False), name=k))
                    if first_idx is None:
                        first_idx = len(lv.children) - 1
                    if k == keep:
                        sel_idx = len(lv.children) - 1
                    await self._mount_field(k, form)
            lv.index = sel_idx if sel_idx is not None else first_idx
        except Exception as e:  # noqa: BLE001 — never a crash screen
            self.notify(f"settings build failed: {type(e).__name__}: {e}", severity="error")

    async def _mount_field(self, key: str, form) -> None:

        fdef = ALL_FIELDS.get(key, ("string", None))
        ftype = fdef[0]
        cur = self._cfg.get(key, "")
        await form.mount(Static(key, classes="field-label"))
        if ftype == "bool":
            w = Switch(value=bool(cur), classes="field-widget")
            self._rendered[key] = bool(cur)
        elif ftype == "choice":
            choices = [str(c) for c in fdef[1]]
            opts = [(c, c) for c in choices]
            install = str(cur) if cur not in ("", None) else choices[0]
            if cur not in ("", None) and install not in choices:
                # the current value rides as an EXTRA option — never silently
                # rewritten to choices[0]. value and label MUST be identical:
                # this Textual builds the legal-value set from the tuple's
                # second slot when they differ (the Illegal-select bug)
                opts = [(install, install)] + opts
            # the ctor IGNORES its value param — construct, then assign
            w = Select(opts, classes="field-widget", allow_blank=False)
            w.value = install
            self._rendered[key] = install
        else:
            install = "" if cur in ("", None) else str(cur)
            w = Input(value=install, classes="field-widget")
            self._rendered[key] = install
        # the widget's OWN field key rides its id (Select.name is read-only
        # in Textual 8.x): "f-<key>" — events read it back
        w.id = f"f-{key}"
        self._editors[key] = w
        await form.mount(w)

    def _fmt(self, key: str, val) -> str:
        if isinstance(val, bool):
            return "ON" if val else "OFF"
        if isinstance(val, float):
            return f"{val:.2f}"
        return str(val) if val != "" else "—"

    # ── events ────────────────────────────────────────────────────────

    def on_list_view_highlighted(self, event) -> None:
        """Scroll the form to the highlighted field's editor."""
        item = event.list_view.highlighted_child
        name = getattr(item, "name", "") or ""
        if not name.startswith("__header__"):
            w = self._editors.get(name)
            if w is not None:
                with contextlib_suppress():
                    w.scroll_visible()

    def on_button_pressed(self, event) -> None:
        if event.button.id == "btn-save":
            self.action_save()
        elif event.button.id == "btn-cancel":
            self.action_cancel()

    def _accept(self, key: str | None, value) -> bool:
        """Echo gate + write-guard: mount-time echoes (value == installed)
        never write; the key must be a real field."""
        if not key or key not in ALL_FIELDS:
            return False
        return not (key in self._rendered and _eq(self._rendered[key], value))

    def _key_of(widget) -> str | None:
        wid = getattr(widget, "id", None) or ""
        return wid.removeprefix("f-") if wid.startswith("f-") else None

    def on_switch_changed(self, event) -> None:
        key = SettingsApp._key_of(event.switch)
        if self._accept(key, event.value):
            self._cfg[key] = bool(event.value)
            self._rendered.pop(key, None)
            self._refresh_row(key)

    def on_select_changed(self, event) -> None:
        key = SettingsApp._key_of(event.select)
        if event.value is not None and self._accept(key, event.value):
            self._cfg[key] = str(event.value)
            self._rendered.pop(key, None)
            self._refresh_row(key)
            if key == "provider":
                self.call_after_refresh(self._rebuild, str(event.value))  # refilters visible fields

    def on_input_changed(self, event) -> None:
        key = SettingsApp._key_of(event.input)
        if not self._accept(key, event.value):
            return
        raw = event.value.strip()
        try:
            fdef = ALL_FIELDS.get(key, ("string",))
            if fdef[0] == "float" and raw:
                lo, hi = fdef[1]
                self._cfg[key] = max(lo, min(hi, float(raw)))
            elif fdef[0] == "int" and raw:
                lo, hi = fdef[1]
                self._cfg[key] = max(lo, min(hi, int(raw)))
            elif fdef[0] == "string":
                self._cfg[key] = raw
        except ValueError:
            pass  # mid-typing; clamped/validated again on save
        self._refresh_row(key)

    def _refresh_row(self, key: str) -> None:
        try:
            from textual.widgets import ListItem

            for item in self.query_one("#list", ListView).children:
                if getattr(item, "name", "") == key and isinstance(item, ListItem):
                    item.children[0].update(f"{key}: {self._fmt(key, self._cfg.get(key, ''))}")
        except Exception:  # noqa: BLE001
            pass

    # ── actions ───────────────────────────────────────────────────────

    def action_save(self) -> None:
        if self._status == "corrupt":
            self.notify(
                f"config.json is corrupt — refusing to save over it. Fix the JSON by hand: {CONFIG_PATH}",
                severity="error",
                timeout=10,
            )
            return
        try:
            save_config(self._cfg)
            self.notify(f"saved → {CONFIG_PATH}")
            self.exit()
        except Exception as e:  # noqa: BLE001
            self.notify(f"save failed: {type(e).__name__}: {e}", severity="error")

    def action_cancel(self) -> None:
        self.notify("exit without saving")
        self.exit()


def contextlib_suppress():
    import contextlib

    return contextlib.suppress(Exception)


def main() -> int:
    if not sys_stdin_tty():
        # Non-interactive (CI, pipes): print the summary, never start a TUI
        cfg = load_config()
        model = cfg.get(f"{cfg.get('provider', '')}_model", "—")
        print(f"settings: {CONFIG_PATH}")
        print(f"  provider: {cfg.get('provider', '—')}   model: {model}")
        print("non-interactive — edit config.json directly or run in a terminal")
        return 0
    SettingsApp().run()
    return 0


def sys_stdin_tty() -> bool:
    import sys

    try:
        return sys.stdin.isatty()
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    raise SystemExit(main())
