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
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, ListItem, ListView, Select, Static, Switch

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


class SettingsApp(App):
    """Two-pane settings editor — list + typed editors, notify-bounded."""

    TITLE = "SUIJIN — Settings"
    CSS = """
    #body { height: 1fr; }
    #list { width: 44%; border: solid $accent; }
    #editor { width: 56%; border: solid $surface; padding: 1 2; }
    #field-title { text-style: bold; color: $text; height: auto; }
    #field-hint { color: $text-muted; height: auto; }
    #editor-widget { margin-top: 1; }
    """
    BINDINGS = [
        ("ctrl+s", "save", "Save + exit"),
        ("escape", "cancel", "Exit without saving"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._cfg = load_config()
        self._cfg.setdefault("provider", "deepseek")
        self._syncing = False  # programmatic widget updates must not echo into config

    # ── layout ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield ListView(id="list")
            with Vertical(id="editor"):
                yield Static("", id="field-title")
                yield Static("", id="field-hint")
                # all three editor widgets pre-mounted, shown one at a time —
                # mounting/removing per selection wedged Textual's message pump
                yield Switch(id="edit-switch")
                yield Select([("—", "—")], id="edit-select", allow_blank=False)  # options set per field
                yield Input(id="edit-input")
        yield Footer()

    def on_mount(self) -> None:
        self._rebuild_list()
        self.query_one("#list", ListView).focus()

    def _rebuild_list(self, keep: str = "") -> None:
        """Grouped field list (section headers + fields for this provider)."""
        try:
            lv = self.query_one("#list", ListView)
            visible = _visible_fields(self._cfg)
            lv.clear()
            from textual.widgets import ListItem as _LI

            sel_item = None
            first_idx = None
            for title, keys in _GROUPS:
                vis = [k for k in keys if k in visible]
                if not vis:
                    continue
                lv.append(_LI(Static(f"[{title}]", markup=False), disabled=True, name=f"__header__{title}"))
                for k in vis:
                    val = self._fmt(k, self._cfg.get(k, ""))
                    item = _LI(Static(f"{k}: {val}", markup=False), name=k)
                    lv.append(item)
                    if first_idx is None:
                        first_idx = len(lv.children) - 1
                    if k == keep:
                        sel_item = len(lv.children) - 1
            lv.index = sel_item if sel_item is not None else first_idx
            self._render_editor()
        except Exception as e:  # noqa: BLE001 — never a crash screen
            self.notify(f"field list failed: {type(e).__name__}: {e}", severity="error")

    def _fmt(self, key: str, val) -> str:
        if isinstance(val, bool):
            return "ON" if val else "OFF"
        if isinstance(val, float):
            return f"{val:.2f}"
        return str(val) if val != "" else "—"

    # ── editor pane ───────────────────────────────────────────────────

    def _render_editor(self) -> None:
        self._syncing = True  # widget .value assignments below are renders, not edits
        try:
            key = self._selected_key()
            title = self.query_one("#field-title", Static)
            hint = self.query_one("#field-hint", Static)
            sw = self.query_one("#edit-switch", Switch)
            sel = self.query_one("#edit-select", Select)
            inp = self.query_one("#edit-input", Input)
            sw.display = sel.display = inp.display = False
            if key is None:
                title.update("select a field")
                hint.update("")
                return
            fdef = ALL_FIELDS.get(key, ("string", None))
            ftype = fdef[0]
            filters = fdef[2] if len(fdef) > 2 else None
            scope = f" · shown for provider '{self._cfg.get('provider', '')}'" if filters else ""
            title.update(key)
            cur = self._cfg.get(key, "")
            if ftype == "bool":
                hint.update("toggle the switch" + scope)
                sw.value = bool(cur)
                sw.display = True
            elif ftype == "choice":
                choices = [str(c) for c in fdef[1]]
                hint.update("pick from the list" + scope)
                sel.set_options([(c, c) for c in choices])
                sel.value = str(cur) if cur in choices else choices[0]
                sel.display = True
            else:
                lohi = fdef[1]
                hint.update((f"range {lohi[0]}–{lohi[1]}, clamped on save" if lohi else "free text") + scope)
                inp.value = self._fmt(key, cur) if cur != "" else ""
                inp.display = True
        finally:
            self._syncing = False
            # let queued render-time events drain past the guard before user edits
            self.call_after_refresh(lambda: setattr(self, "_syncing", False))

    def _selected_key(self) -> str | None:
        lv = self.query_one("#list", ListView)
        item = lv.highlighted_child
        if item is None:
            return None
        name = getattr(item, "name", "") or ""
        return None if name.startswith("__header__") else name

    # ── events ────────────────────────────────────────────────────────

    def on_list_view_highlighted(self, event) -> None:
        self._render_editor()

    def on_switch_changed(self, event) -> None:
        if self._syncing:
            return
        key = self._selected_key()
        if key:
            self._cfg[key] = bool(event.value)
            self._refresh_row(key)

    def on_select_changed(self, event) -> None:
        if self._syncing:
            return
        key = self._selected_key()
        fdef = ALL_FIELDS.get(key) if key else None
        valid = fdef and fdef[0] == "choice" and str(event.value) in [str(c) for c in fdef[1]]
        if key and event.value is not None and valid:
            self._cfg[key] = str(event.value)
            self._refresh_row(key)
            if key == "provider":
                self._rebuild_list(keep=str(event.value))  # provider filters the visible fields

    def on_input_changed(self, event) -> None:
        if self._syncing:
            return
        key = self._selected_key()
        if not key or not self.query_one("#edit-input", Input).display:
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
            for item in self.query_one("#list", ListView).children:
                if getattr(item, "name", "") == key and isinstance(item, ListItem):
                    item.children[0].update(f"{key}: {self._fmt(key, self._cfg.get(key, ''))}")
        except Exception:  # noqa: BLE001
            pass

    # ── actions ───────────────────────────────────────────────────────

    def action_save(self) -> None:
        try:
            save_config(self._cfg)
            self.notify(f"saved → {CONFIG_PATH}")
            self.exit()
        except Exception as e:  # noqa: BLE001
            self.notify(f"save failed: {type(e).__name__}: {e}", severity="error")

    def action_cancel(self) -> None:
        self.notify("exit without saving")
        self.exit()


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
