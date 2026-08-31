#!/usr/bin/env python3
"""
Suijin Settings TUI (curses)
=============================
Arrow keys navigate, Enter edits, 'q' saves, Esc cancels.
Dynamically shows fields relevant to the selected provider.
"""

import curses
import json
import os
from collections import OrderedDict
from pathlib import Path

# The package-level config (suijin/config.json) — resolved from this file's
# location so it works from the dev symlink too (dirname(__file__) here is
# modules/console/lib/, where no config ever lived; the crash-on-open bug).
CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "config.json"
)


def _registry_provider_choices():
    """Registry providers + any custom:<name> entries in config.json."""
    choices = []
    try:
        from suijin.modules.providers.lib.registry import CLOUD_KEYS, LOCAL_KEYS

        choices = CLOUD_KEYS + LOCAL_KEYS
    except Exception:  # noqa: BLE001 — Settings must open even headless
        pass
    try:
        cfg = json.loads(Path(CONFIG_PATH).read_text()) if os.path.exists(CONFIG_PATH) else {}
        for entry in cfg.get("custom_providers") or []:
            name = str(entry.get("name", "")).strip()
            if name:
                choices.append(f"custom:{name}")
    except Exception:  # noqa: BLE001
        pass
    return choices


# ---- Field Definitions ----
# Each field: (type, extra) where extra depends on type:
#   "string" -> no extra
#   "choice" -> list of options
#   "float"  -> (min, max)
#   "int"    -> (min, max)
#   "bool"   -> no extra
# Fields can have an optional third element: provider filter.
# If set, this field only shows when the listed provider(s) are selected.

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
        # burns plan credits, Lite/Pro/Max). "paas" = pay-as-you-go per-token
        # USD billing. Same ZAI_API_KEY works on both, but a plan key on paas
        # (or a PAYG key on coding) returns 403.
        ("zai_endpoint", ("choice", ["coding", "paas"], ["zai"])),
        ("zai_model", ("choice", ["glm-5.3", "glm-5-turbo", "glm-4.7", "glm-5.1", "glm-4.6"], ["zai"])),
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
        ("temperature", ("float", (0.0, 2.0))),
        ("max_tokens_per_request", ("int", (1, 128000))),
        ("max_iterations", ("int", (1, 500))),
        ("supervisor_model_id", ("string",)),
        ("supervisor_interval", ("int", (1, 100))),
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


def _visible_fields(config):
    """Return the OrderedDict of fields that apply to the current provider."""
    provider = config.get("provider", "deepseek")
    visible = OrderedDict()
    for key, field_def in ALL_FIELDS.items():
        filters = field_def[2] if len(field_def) > 2 else None
        if filters is not None and provider not in filters:
            continue
        visible[key] = field_def
    return visible


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Config not found at {CONFIG_PATH}")
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=4)


def run(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(0)
    stdscr.keypad(1)
    curses.start_color()
    curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)  # highlight
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)  # title
    curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)  # edit mode
    curses.init_pair(4, curses.COLOR_RED, curses.COLOR_BLACK)  # sensitive

    config = load_config()
    # Ensure defaults
    config.setdefault("provider", "deepseek")

    # Build visible field list based on provider
    def rebuild_fields():
        visible = _visible_fields(config)
        return list(visible.keys()), visible

    keys, fields = rebuild_fields()
    row = 0
    status = ""

    def draw():
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        y = 1
        provider = config.get("provider", "deepseek").upper()
        title = f" Suijin Settings — {provider} "
        start_x = max(0, (w // 2) - (len(title) // 2))
        stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
        stdscr.addstr(y, start_x, title)
        stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
        y += 2
        stdscr.addstr(y, 2, " move  Enter edit  q save  Esc cancel", curses.A_DIM)
        y += 1
        stdscr.addstr(y, 2, "─" * (w - 4))
        y += 1

        for idx, key in enumerate(keys):
            if y >= h - 2:
                break
            val = config.get(key, "")
            display = val
            if isinstance(display, float):
                display = f"{display:.2f}"
            if isinstance(val, bool):
                display = "ON" if val else "OFF"
            line = f"  {key}: {display}"

            if idx == row:
                stdscr.attron(curses.color_pair(1) | curses.A_REVERSE)
                stdscr.addstr(y, 2, line.ljust(w - 4))
                stdscr.attroff(curses.color_pair(1) | curses.A_REVERSE)
            else:
                stdscr.addstr(y, 2, line.ljust(w - 4), curses.A_NORMAL)
            y += 1

        if status:
            stdscr.addstr(h - 2, 2, status[: w - 4], curses.A_BOLD)
        stdscr.refresh()

    while True:
        draw()
        key = stdscr.getch()

        if key == curses.KEY_UP and row > 0:
            row -= 1
        elif key == curses.KEY_DOWN and row < len(keys) - 1:
            row += 1
        elif key == ord("q") or key == ord("Q"):
            save_config(config)
            status = "Saved."
            draw()
            curses.napms(1000)
            break
        elif key == 27:  # Esc
            status = "Exit without saving."
            draw()
            curses.napms(1000)
            break
        elif key == 10:  # Enter
            sel_key = keys[row]
            field_def = fields[sel_key]
            field_type = field_def[0]

            # ---------- BOOLEAN TOGGLE ----------
            if field_type == "bool":
                current = config.get(sel_key, False)
                config[sel_key] = not current
                status = f"{sel_key} toggled to {'ON' if config[sel_key] else 'OFF'}"
                continue

            elif field_type == "choice":
                choices = field_def[1]
                ci = 0
                if config.get(sel_key) in choices:
                    ci = choices.index(config[sel_key])
                while True:
                    stdscr.clear()
                    h2, w2 = stdscr.getmaxyx()
                    y2 = 3
                    stdscr.addstr(y2, 5, f"Choose {sel_key}:")
                    y2 += 2
                    for i, c in enumerate(choices):
                        if i == ci:
                            stdscr.attron(curses.color_pair(1) | curses.A_REVERSE)
                            stdscr.addstr(y2, 7, f"> {c}")
                            stdscr.attroff(curses.color_pair(1) | curses.A_REVERSE)
                        else:
                            stdscr.addstr(y2, 7, f"  {c}")
                        y2 += 1
                    stdscr.refresh()
                    k = stdscr.getch()
                    if k == curses.KEY_UP and ci > 0:
                        ci -= 1
                    elif k == curses.KEY_DOWN and ci < len(choices) - 1:
                        ci += 1
                    elif k == 10:
                        config[sel_key] = choices[ci]
                        # If provider changed, rebuild visible fields
                        if sel_key == "provider":
                            keys_new, fields_new = rebuild_fields()
                            keys.clear()
                            keys.extend(keys_new)
                            fields.clear()
                            fields.update(fields_new)
                            if row >= len(keys):
                                row = len(keys) - 1
                        status = f"{sel_key} set to {choices[ci]}"
                        break
                    elif k == 27:
                        break
            else:
                # Text / numeric input
                stdscr.clear()
                h2, w2 = stdscr.getmaxyx()
                stdscr.addstr(3, 3, f"New value for {sel_key}:")
                stdscr.addstr(4, 3, "(press Enter to confirm)")
                curses.echo()
                curses.curs_set(1)
                stdscr.move(4, 3 + 30)
                input_bytes = stdscr.getstr(4, 3 + 30, 256)
                curses.noecho()
                curses.curs_set(0)
                try:
                    new_val = input_bytes.decode("utf-8").strip()
                    if field_type == "float":
                        lo, hi = field_def[1]
                        val = max(lo, min(hi, float(new_val)))
                        config[sel_key] = val
                    elif field_type == "int":
                        lo, hi = field_def[1]
                        val = max(lo, min(hi, int(new_val)))
                        config[sel_key] = val
                    else:
                        config[sel_key] = new_val
                except (ValueError, KeyError):
                    pass


def main():
    curses.wrapper(run)


if __name__ == "__main__":
    main()
