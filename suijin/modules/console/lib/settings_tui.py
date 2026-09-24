"""Suijin Settings — a Rich TUI (no external TUI framework).

The house pattern, like every other console surface: rich Console +
Panel + Table, one owner of stdin, and every action failure-isolated. The
editor is line-oriented, so the whole class of render-echo bugs
(navigation writing into config) is gone by construction — a value
changes only when the operator types it and presses Enter.

  ↑/↓ or j/k   move        Enter  edit the field
  m            fetch the provider's LIVE model ids, pick one
  s            save        q / Ctrl+C   quit (nothing saved unless s)

NOTHING IS HARDCODED: the provider list is built from the provider
registry (50 sources: cloud, aggregator, local) plus the bespoke
code-path providers discovered from the dispatch itself, plus any
custom:<name> boxes in config.json. Model ids are FETCHED from the
provider's own /models endpoint (with a timeout and an honest failure
line) — never a stale static list. Off-TTY prints the same summary:
Settings always opens, even headless.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import sys
import time
from collections import OrderedDict
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

# The package-level config (suijin/config.json) — resolved from this file's
# location so it works from the dev symlink too.
CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "config.json"
)

#: how long a live model fetch may take before we say so and move on
MODEL_FETCH_TIMEOUT = 8.0
#: a model list longer than this is summarized (the pick prompt scrolls)
MODEL_LIST_CAP = 40
#: the config key holding the model for a provider ("<provider>_model")
MODEL_KEY_SUFFIX = "_model"


def _bespoke_provider_keys() -> list[str]:
    """Providers with their own code path (`if provider == ...` branches in
    the provider layer) — DISCOVERED from that dispatch, not a hand-kept
    list, so a new bespoke provider shows up here on its own."""
    keys: list[str] = []
    with contextlib.suppress(Exception):
        from suijin.modules.providers.lib import __file__ as prov_file

        tree = ast.parse(Path(prov_file).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare) or len(node.ops) != 1:
                continue
            op = node.ops[0]
            if not isinstance(op, (ast.Eq, ast.In)):
                continue
            left = node.left
            name = left.id if isinstance(left, ast.Name) else None
            if name not in ("provider", "p", "prov", "_provider"):
                continue
            for cmp_node in node.comparators:
                if isinstance(cmp_node, ast.Constant) and isinstance(cmp_node.value, str):
                    keys.append(cmp_node.value)
                elif isinstance(cmp_node, (ast.Tuple, ast.List, ast.Set)):
                    for elt in cmp_node.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            keys.append(elt.value)
    return sorted(dict.fromkeys(keys))


def _custom_provider_choices(config: dict | None = None) -> list[str]:
    """custom:<name> boxes the operator declared in config.json."""
    out = []
    try:
        cfg = config if isinstance(config, dict) else {}
        if not cfg:
            cfg = json.loads(Path(CONFIG_PATH).read_text()) if os.path.isfile(CONFIG_PATH) else {}
        for entry in cfg.get("custom_providers") or []:
            name = str(entry.get("name", "")).strip()
            if name:
                out.append(f"custom:{name}")
    except Exception:  # noqa: BLE001 — Settings must open even headless
        pass
    return out


def provider_choices(config: dict | None = None) -> list[str]:
    """Every provider the operator can pick: the registry (cloud +
    aggregator + local), the bespoke code-path providers, and customs.
    Fully dynamic — a new registry row appears here with no edit."""
    out: list[str] = []
    with contextlib.suppress(Exception):
        from suijin.modules.providers.lib.registry import CHINA_KEYS, CLOUD_KEYS, LOCAL_KEYS, WESTERN_KEYS

        out.extend(CLOUD_KEYS + CHINA_KEYS + WESTERN_KEYS + LOCAL_KEYS)
    out.extend(_bespoke_provider_keys())
    out.extend(_custom_provider_choices(config))
    return list(dict.fromkeys(k for k in out if k))


# ---- Field definitions: (type, extra[, provider filter]) ----
# Types: choice | string | model | bool | int | float
#   choice = a fixed set (endpoints, posture — stable enums)
#   model  = a MODEL ID: freeform, with `m` fetching the live list
# The provider list is NOT here — it is dynamic (registry + bespoke +
# customs); see provider_choices().
ALL_FIELDS = OrderedDict(
    [
        # ---- Provider (choices resolved at render time) ----
        ("provider", ("provider", None)),
        # ---- Model ids: freeform + live fetch. The field name doubles as
        # the config key ("zai_model" is the zai model). ----
        ("deepseek_model", ("model", None, ["deepseek"])),
        # zai_endpoint: "coding" = GLM Coding Plan subscription quota (default;
        # burns plan credits). "paas" = pay-as-you-go per-token USD billing.
        # Same key, but a plan key on paas (or PAYG on coding) returns 403.
        ("zai_endpoint", ("choice", ["coding", "paas"], ["zai"])),
        ("zai_model", ("model", None, ["zai"])),
        ("gemini_model", ("model", None, ["gemini"])),
        ("anthropic_model", ("model", None, ["anthropic"])),
        ("final_model_id", ("model", None, ["huggingface"])),
        ("sentinel_model_id", ("model", None, ["huggingface"])),
        # ---- Universal fields (all providers) ----
        ("posture", ("choice", ["assertive", "recon"])),
        ("temperature", ("float", (0.0, 2.0))),
        ("max_tokens_per_request", ("int", (1, 128000))),
        ("context_window", ("int", (0, 10_000_000))),
        ("max_iterations", ("int", (1, 1000000))),
        ("librarian_interval", ("int", (1, 1000))),
        # ---- Cost guardrails ----
        ("cost_alert_usd", ("float", (0.0, 1000.0))),
        ("cost_budget_usd", ("float", (0.0, 1000.0))),
        ("cost_hard_cap_usd", ("float", (0.0, 1000.0))),
        # ---- Proxy ----
        ("proxy_url", ("string",)),
        # ---- Operational Modes ----
        ("mode_hitl", ("bool",)),
        ("mode_deploy_subagent", ("bool",)),
        ("mode_audit_trail", ("bool",)),
        ("subagent_count", ("int", (1, 5))),
        # ---- Launch mode ----
        ("launch_mode", ("choice", ["daemon", "tui"])),
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
            "librarian_interval",
            "launch_mode",
        ],
    ),
    ("Cost guardrails", ["cost_alert_usd", "cost_budget_usd", "cost_hard_cap_usd"]),
    ("Proxy", ["proxy_url"]),
    (
        "Operational modes",
        ["mode_hitl", "mode_deploy_subagent", "mode_audit_trail", "subagent_count"],
    ),
    ("Integrations", ["metasploit_rpc_host", "metasploit_rpc_port"]),
]


def is_model_field(key: str) -> bool:
    """True when the field holds a model id: a declared `model` field, or
    the `<provider>_model` field synthesized for a registry/local/custom
    provider. Dynamic — a provider added to the registry qualifies for
    free."""
    fdef = ALL_FIELDS.get(key)
    if fdef and fdef[0] == "model":
        return True
    return bool(key) and key.endswith(MODEL_KEY_SUFFIX)


def model_field_for_provider(provider: str, config: dict | None = None) -> str | None:
    """Which config key holds THIS provider's model — resolved, not a map:
    the conventional `<provider>_model` first, then any visible model field
    (so a registry provider like `opencode` works with no code change)."""
    config = dict(config or {})
    provider = str(provider or config.get("provider") or "")
    if not provider:
        return None
    conventional = f"{provider}{MODEL_KEY_SUFFIX}"
    if conventional in ALL_FIELDS:
        return conventional
    # a registry/local provider: reuse the first model field of the same kind
    for key, fdef in ALL_FIELDS.items():
        if fdef[0] == "model":
            return key
    return None


def load_config() -> dict:
    if not os.path.isfile(CONFIG_PATH):  # is_file: a bind-mount can leave a directory
        return {}
    try:
        cfg = json.loads(Path(CONFIG_PATH).read_text())
        return cfg if isinstance(cfg, dict) else {}
    except Exception:  # noqa: BLE001 — Settings must open on a corrupt config
        return {}


def load_state() -> tuple[dict, str]:
    """(config, status) where status ∈ ok | missing | corrupt — the editor
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
    """The fields for the CURRENT provider.

    Bespoke providers have declared model fields; every OTHER provider
    (the 50-source registry, local boxes, custom:<name>) gets its
    `<provider>_model` field synthesized on the fly — so a provider added
    to the registry is fully editable here with no code change."""
    provider = str(config.get("provider") or "deepseek")
    visible = OrderedDict()
    for key, field_def in ALL_FIELDS.items():
        filters = field_def[2] if len(field_def) > 2 else None
        if filters is not None and provider not in filters:
            continue
        visible[key] = field_def
    # a provider with no declared model field: add its own
    if not any(fdef[0] == "model" for fdef in visible.values()):
        mkey = f"{provider}{MODEL_KEY_SUFFIX}" if provider else ""
        if mkey and mkey not in visible:
            visible[mkey] = ("model", None)
    return visible


def _default_model_hint(provider: str) -> str:
    """The provider's registry default model (a hint in the prompt)."""
    with contextlib.suppress(Exception):
        from suijin.modules.providers.lib.registry import PROVIDER_REGISTRY

        spec = PROVIDER_REGISTRY.get(str(provider or ""))
        if spec is not None:
            return str(spec.default_model or "")
    return ""


def _eq(a, b) -> bool:
    """Loose echo comparison — '500' == 500 == 500.0 for render-echo checks."""
    try:
        return str(a) == str(b)
    except Exception:  # noqa: BLE001
        return a == b


def _fmt(key: str, value) -> str:
    """Render one value the way the editor shows it."""
    fdef = ALL_FIELDS.get(key, ("string",))
    kind = fdef[0]
    if kind == "bool":
        return "on" if value else "off"
    if value is None or value == "":
        return "[dim](unset)[/dim]"
    return str(value)


# ── live model ids ──────────────────────────────────────────────────────


def _http_get_json(url: str, headers: dict, timeout: float) -> dict:
    """The ONE seam the fetcher uses (tests stub this — no network)."""
    import requests as req

    resp = req.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _provider_spec(provider: str):
    """The registry spec for a provider key, or None (local/custom)."""
    try:
        from suijin.modules.providers.lib.registry import PROVIDER_REGISTRY

        return PROVIDER_REGISTRY.get(str(provider or ""))
    except Exception:  # noqa: BLE001
        return None


def fetch_model_ids(provider: str, config: dict | None = None) -> tuple[list[str], str]:
    """(model_ids, note) from the provider's live /models endpoint.

    Never raises: a dead network, a missing key or a gateway with an
    exotic schema returns ([], a human reason). OpenAI-compatible shape is
    `data[].id`; a bare list and a `models[].id` shape are also accepted.

    The endpoint is NOT known here — the provider layer owns provider
    wiring and answers `provider_models_endpoint()`, so a new provider
    needs no edit in this file.
    """
    config = dict(config or {})
    try:
        from suijin.modules.providers.lib import provider_models_endpoint

        base, headers = provider_models_endpoint(provider, config)
    except Exception as e:  # noqa: BLE001 — the layer may be mid-import
        return [], f"cannot resolve the endpoint: {type(e).__name__}: {str(e)[:100]}"
    if not base:
        return [], str(headers or "no model-list endpoint for this provider")
    url = f"{str(base).rstrip('/')}/models"

    started = time.time()
    try:
        data = _http_get_json(url, dict(headers or {}), MODEL_FETCH_TIMEOUT)
    except Exception as e:  # noqa: BLE001 — a fetch failure is information, not a crash
        hint = ""
        if not (headers or {}).get("Authorization"):
            hint = " (no API key sent — some gateways require one)"
        return [], f"fetch failed: {type(e).__name__}: {str(e)[:120]}{hint}"

    ids: list[str] = []
    if isinstance(data, dict):
        rows = data.get("data") if isinstance(data.get("data"), list) else data.get("models")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    mid = row.get("id") or row.get("name") or row.get("model")
                    if mid:
                        ids.append(str(mid))
                elif isinstance(row, str):
                    ids.append(row)
    elif isinstance(data, list):
        ids = [str(r.get("id") if isinstance(r, dict) else r) for r in data]

    ids = sorted(dict.fromkeys(i for i in ids if i))
    took = time.time() - started
    if not ids:
        return [], f"no model ids in the response from {url}"
    return ids, f"{len(ids)} model(s) from {url} in {took:.1f}s"


# ── the Rich editor ─────────────────────────────────────────────────────


def _key_stream(console: Console):
    """A single-key reader over the TTY (j/k + arrows). Falls back to line
    mode off-POSIX / when cbreak is unavailable. The terminal is always
    restored — a crash here must never leave the shell without echo."""
    try:
        import termios
        import tty
    except ImportError:  # pragma: no cover — Windows
        return None
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return None
    except Exception:  # noqa: BLE001
        return None

    def _read() -> str:
        try:
            old = termios.tcgetattr(fd)
        except Exception:  # noqa: BLE001
            return ""
        try:
            tty.setcbreak(fd)  # ISIG stays ON: Ctrl+C still signals
            ch = sys.stdin.read(1)
            if ch == "\x1b":  # escape sequence: arrows
                nxt = sys.stdin.read(2)
                return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(nxt, "esc")
            return {"\r": "enter", "\n": "enter"}.get(ch, ch)
        except Exception:  # noqa: BLE001
            return ""
        finally:
            with contextlib.suppress(Exception):
                termios.tcsetattr(fd, termios.TCSADRAIN, old)

    return _read


def _row_items(visible: "OrderedDict[str, tuple]"):
    """[(group, key)] in display order, honouring _GROUPS then leftovers.
    The Provider group also absorbs every model field (including the
    `<provider>_model` one synthesized for a registry provider) so the
    model sits next to the provider, not at the bottom of the list."""
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for group, keys in _GROUPS:
        group_keys = list(keys)
        if group == "Provider":
            group_keys += [k for k, f in visible.items() if f[0] == "model" and k not in group_keys]
        for key in group_keys:
            if key in visible and key not in seen:
                items.append((group, key))
                seen.add(key)
    for key, fdef in visible.items():
        if key not in seen:
            items.append(("Provider" if fdef[0] in ("model", "provider") else "Other", key))
    return items


def _render(console: Console, config: dict, visible, items, cursor: int, note: str = "") -> None:
    table = Table(
        title="SUIJIN — Settings",
        title_style="bold white",
        header_style="dim",
        expand=False,
        pad_edge=False,
    )
    table.add_column("field", style="cyan", no_wrap=True)
    table.add_column("value", overflow="fold")
    for idx, (_group, key) in enumerate(items):
        marker = "[bold #e6b47c]›[/] " if idx == cursor else "  "
        name = f"{marker}{key}"
        if idx == cursor:
            name = f"[reverse]{name}[/reverse]"
        suffix = " [dim]· live ids (m)[/dim]" if is_model_field(key) else ""
        table.add_row(f"{name}{suffix}", _fmt(key, config.get(key, "")))
    console.print(table)
    if visible_provider_note(config):
        console.print(f"  [dim]{visible_provider_note(config)}[/dim]")
    if note:
        console.print(f"  [dim]{note}[/dim]")
    console.print("  [dim]↑/↓ or j/k move · Enter edit · m fetch live model ids · s save · q quit[/dim]")


def visible_provider_note(config: dict) -> str:
    """A one-line hint about the current provider: where its model ids come
    from, and whether a key is on file (asked of the provider layer)."""
    provider = str(config.get("provider") or "")
    if not provider:
        return "no provider selected"
    with contextlib.suppress(Exception):
        from suijin.modules.providers.lib import provider_models_endpoint

        base, headers = provider_models_endpoint(provider, config)
        if not base:
            return f"{provider} — {headers}"
        keyed = bool((headers or {}).get("Authorization"))
        return f"{provider} — live ids at {str(base).rstrip('/')}/models ({'key on file' if keyed else 'no key sent'})"
    return f"{provider} — model source unknown"


def _edit_field(console: Console, config: dict, key: str) -> tuple[str, bool]:
    """Prompt for one field, validating + clamping. Returns (note, apply?)."""
    fdef = ALL_FIELDS.get(key, ("string",))
    kind = fdef[0]
    current = config.get(key, "")
    try:
        if kind == "bool":
            raw = Prompt.ask(
                f"{key} (on/off)", console=console, default="on" if current else "off", choices=["on", "off"]
            )
            return (f"{key} = {raw}", True) if raw else ("", False)
        if kind == "provider":
            # dynamic: registry (50 sources) + bespoke + custom:<name>
            choices = provider_choices(config)
            if current not in (None, "") and str(current) not in choices:
                choices = [str(current)] + choices
            raw = Prompt.ask(
                f"{key} ({len(choices)} available)", console=console, default=str(current or ""), choices=choices
            )
            return (f"{key} = {raw}", True) if raw else ("", False)
        if kind == "choice":
            choices = list(fdef[1] or [])
            # a value OUTSIDE the static list is kept and offered first
            if current not in (None, "") and str(current) not in choices:
                choices = [str(current)] + choices
            raw = Prompt.ask(key, console=console, default=str(current or ""), choices=choices)
            return (f"{key} = {raw}", True) if raw else ("", False)
        if kind == "model":
            # freeform — the live list is one keypress away (m)
            hint = _default_model_hint(str(config.get("provider") or ""))
            label = f"{key} [dim](m = fetch live ids{f'; default {hint}' if hint else ''})[/dim]"
            raw = Prompt.ask(label, console=console, default=str(current or hint or ""))
            return (f"{key} = {raw}", True) if raw else ("", False)
        raw = Prompt.ask(f"{key} [{_fmt(key, current)}]", console=console, default=str(current or ""))
        if raw == "" or raw is None:
            return ("", False)
        if kind == "int":
            lo, hi = fdef[1]
            return f"{key} = {max(lo, min(hi, int(raw)))}", True
        if kind == "float":
            lo, hi = fdef[1]
            return f"{key} = {max(lo, min(hi, float(raw)))}", True
        return f"{key} = {raw}", True
    except (ValueError, TypeError):
        return f"{key}: value rejected — keeping the current one", False
    except (EOFError, KeyboardInterrupt):
        return "", False


def _apply(console: Console, config: dict, key: str, raw: str) -> bool:
    """Parse+clamp one raw value into config. True when it changed."""
    fdef = ALL_FIELDS.get(key, ("string",))
    kind = fdef[0]
    raw = str(raw).strip()
    if kind == "bool":
        val = raw.lower() in ("1", "true", "on", "yes", "enable", "enabled")
    elif kind == "int":
        lo, hi = fdef[1]
        val = max(lo, min(hi, int(raw)))
    elif kind == "float":
        lo, hi = fdef[1]
        val = max(lo, min(hi, float(raw)))
    else:
        val = raw
    changed = not _eq(config.get(key), val)
    config[key] = val
    return changed


def _pick_model(console: Console, config: dict, key: str) -> str:
    """Fetch the provider's LIVE model ids and pick one. Returns a note.

    The provider comes from the current config (not a hardcoded map), so a
    registry provider added tomorrow is fetchable today."""
    if not is_model_field(key):
        return f"{key} is not a model field (press Enter to type an id)"
    provider = str(config.get("provider") or "")
    if not provider:
        return "no provider selected — set `provider` first"
    ids, note = fetch_model_ids(provider, config)
    if not ids:
        return note
    shown = ids[:MODEL_LIST_CAP]
    try:
        raw = Prompt.ask(
            f"{provider} model ids ({len(ids)}) — pick one, or type an id",
            console=console,
            default=str(config.get(key, "")),
            choices=shown + ([str(config.get(key))] if config.get(key) not in shown else []),
        )
    except (EOFError, KeyboardInterrupt):
        return note
    if not raw:
        return note
    changed = _apply(console, config, key, raw)
    return f"{key} = {raw}" + ("  [dim](changed)[/dim]" if changed else "  [dim](unchanged)[/dim]") + f" — {note}"


def run_editor(console: Console) -> int:
    """The interactive loop. Owns stdin while it runs; always returns."""
    config, status = load_state()
    if status == "corrupt":
        console.print(
            Panel.fit(
                f"config.json is CORRUPT — {CONFIG_PATH}\n"
                "fix the JSON by hand (or move the file aside); Settings will not overwrite it.",
                title=" SETTINGS ",
                border_style="red",
            )
        )
    console.print(Panel.fit(f"[bold white]{CONFIG_PATH}[/]", title=" SETTINGS ", border_style="#30363d"))
    read_key = _key_stream(console)
    items = _row_items(_visible_fields(config))
    cursor = 0
    note = ""
    try:
        while True:
            visible = _visible_fields(config)
            items = _row_items(visible)
            cursor = max(0, min(cursor, len(items) - 1))
            _render(console, config, visible, items, cursor, note)
            note = ""
            if not items:
                return 0
            if read_key is None:
                # line mode: "n" next, "e <key> <value>" edit, "m <key>" models,
                # "s" save, "q" quit
                try:
                    line = console.input("[dim]n / e <field> <value> / m <field> / s / q[/] ").strip()
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[dim]exit without saving[/dim]")
                    return 0
                low = line.lower()
                if low in ("q", "quit", ""):
                    console.print("[dim]exit without saving[/dim]")
                    return 0
                if low == "s":
                    return _save(console, config, status)
                if low.startswith("e "):
                    _, key, raw = (line.split(" ", 2) + [""])[:3]
                    try:
                        _apply(console, config, key.strip(), raw)
                    except Exception as e:  # noqa: BLE001
                        note = f"{key}: {e}"
                elif low.startswith("m "):
                    note = _pick_model(console, config, line.split(" ", 1)[1].strip())
                elif low == "n":
                    cursor += 1
                continue
            key = read_key()
            if key in ("q", "Q", "\x03"):
                console.print("\n[dim]exit without saving[/dim]")
                return 0
            if key == "up" or key == "k":
                cursor = (cursor - 1) % len(items)
            elif key == "down" or key == "j":
                cursor = (cursor + 1) % len(items)
            elif key in ("s", "S"):
                return _save(console, config, status)
            elif key in ("enter", "e"):
                field = items[cursor][1]
                note, _ = _edit_field(console, config, field)
            elif key in ("m", "M"):
                note = _pick_model(console, config, items[cursor][1])
    finally:
        # the terminal is restored by the key stream itself; this is the
        # last-resort guarantee for a crash inside the loop
        with contextlib.suppress(Exception):
            sys.stdout.write("\n")
            sys.stdout.flush()


def _save(console: Console, config: dict, status: str) -> int:
    if status == "corrupt":
        console.print(f"[bold red]refusing to save[/] — config.json is corrupt; fix it by hand: {CONFIG_PATH}")
        return 1
    try:
        save_config(config)
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]save failed[/] — {type(e).__name__}: {e}")
        return 1
    console.print(f"[green]saved[/] → {CONFIG_PATH}")
    return 0


def main() -> int:
    if not sys_stdin_tty():
        # Non-interactive (CI, pipes): print the summary, never start a TUI
        cfg = load_config()
        model = cfg.get(f"{cfg.get('provider', '')}_model", "—")
        print(f"settings: {CONFIG_PATH}")
        print(f"  provider: {cfg.get('provider', '—')}   model: {model}")
        print("non-interactive — edit config.json directly or run in a terminal")
        return 0
    console = Console()
    try:
        return run_editor(console)
    except KeyboardInterrupt:  # Ctrl+C at the prompt — quiet exit
        console.print("\n[dim]cancelled[/dim]")
        return 130
    except Exception as e:  # noqa: BLE001 — Settings must never traceback
        console.print(f"[bold red]settings error[/] — {type(e).__name__}: {e}")
        return 1


def sys_stdin_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    raise SystemExit(main())
