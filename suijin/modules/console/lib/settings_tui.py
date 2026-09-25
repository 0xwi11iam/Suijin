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


#: the synthetic row that shows/edits the provider's API key. It is not a
#: config key: a secret never goes in config.json (that file is snapshotted
#: into bundles). It is written to the .env file by the provider layer.
API_KEY_ROW = "api_key"


def provider_api_key_env(provider: str) -> str:
    """Which env var holds this provider's key (resolved by the provider
    layer — never named in this file)."""
    with contextlib.suppress(Exception):
        from suijin.modules.providers.lib import provider_key_env

        return str(provider_key_env(provider) or "")
    return ""


def api_key_state(config: dict) -> str:
    """What the row shows: NEVER the key, only whether one is on file."""
    env_name = provider_api_key_env(str(config.get("provider") or ""))
    if not env_name:
        return "n/a — this provider needs no key"
    with contextlib.suppress(Exception):
        from suijin.modules.providers.lib import get_provider_key

        return (
            f"set in {env_name}  (••••••••)"
            if get_provider_key(config.get("provider"))
            else f"not set — add {env_name}"
        )
    return f"not set — add {env_name}"


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
                # the provider's key sits right under its model row
                if is_model_field(key) and visible:
                    items.append((group, API_KEY_ROW))
                    seen.add(API_KEY_ROW)
    for key, fdef in visible.items():
        if key not in seen:
            items.append(("Provider" if fdef[0] in ("model", "provider") else "Other", key))
    return items


# ── reading it like a human ──────────────────────────────────────────────
#
# A person opens Settings to answer "what am I running?" and to change ONE
# thing. So the screen leads with the answer (a plain-language summary),
# groups the fields the way a person thinks (which model / how far it may
# run / what it may spend), spells every value out ("disabled", "100,000",
# "not set") and never shows a raw config key as the label.

#: (config key) → (human label, what it does, section)
#: Sections are ordered the way an operator reasons: which model, how far,
#: what it may do, what it may spend, then the rest.
FIELD_INFO = {
    # -- which model --
    "provider": ("Provider", "which company serves the model", "model"),
    "deepseek_model": ("Model", "the model DeepSeek serves", "model"),
    "zai_model": ("Model", "the model z.ai serves", "model"),
    "gemini_model": ("Model", "the model Gemini serves", "model"),
    "anthropic_model": ("Model", "the model Anthropic serves", "model"),
    "final_model_id": ("Main model", "the HuggingFace model the agent runs on", "model"),
    "sentinel_model_id": ("Sentinel model", "the second opinion on every finding", "model"),
    "zai_endpoint": ("z.ai plan", "coding plan (monthly) or pay-per-token", "model"),
    # -- how far it may run --
    "max_iterations": ("Iteration cap", "hard ceiling on think→act→respond cycles", "limits"),
    "max_tokens_per_request": ("Reply size", "max tokens per model response", "limits"),
    "context_window": ("Context window", "0 = ask the model registry (1M fallback)", "limits"),
    "librarian_interval": ("Memory digest every", "how often observations are distilled", "limits"),
    "temperature": ("Creativity", "0 = strict and literal, 2 = loose", "limits"),
    "posture": ("Posture", "recon watches; assertive acts", "limits"),
    "launch_mode": ("Launch", "daemon keeps running if this console closes; tui stays here", "limits"),
    # -- what it may do --
    "mode_hitl": ("Check with me first", "ask before irreversible actions", "conduct"),
    "mode_deploy_subagent": ("Hire subagents", "let the agent delegate in parallel", "conduct"),
    "mode_audit_trail": ("Audit trail", "record every decision and action", "conduct"),
    "subagent_count": ("Subagents at once", "1–5 parallel workers", "conduct"),
    "proxy_url": ("Proxy", "route traffic through http://host:port", "conduct"),
    # -- what it may spend --
    "cost_alert_usd": ("Warn me at", "off means it never warns you", "spend"),
    "cost_budget_usd": ("Soft cap", "off means the agent is told, not stopped", "spend"),
    "cost_hard_cap_usd": ("Hard cap", "off means nothing can stop a runaway", "spend"),
    # -- plumbing --
    "metasploit_rpc_host": ("Metasploit host", "where the Metasploit RPC server listens", "advanced"),
    "metasploit_rpc_port": ("Metasploit port", "its TCP port", "advanced"),
}

#: section → (title, one-line purpose)
SECTIONS = [
    ("model", "Model", "which company and which model"),
    ("limits", "Limits", "how far and how long a run may go"),
    ("conduct", "Conduct", "what the agent may do on its own"),
    ("spend", "Spending", "what the run may cost"),
    ("advanced", "Advanced", "plumbing you rarely touch"),
]


def human_label(key: str) -> str:
    return FIELD_INFO.get(key, (key.replace("_", " "), "", "advanced"))[0]


def human_help(key: str) -> str:
    return FIELD_INFO.get(key, (key, "", ""))[1]


def human_value(key: str, config: dict) -> str:
    """The value, said the way a person says it. The zero-means cases are
    the whole point: '0.0' reads as free, it actually means unlimited."""
    raw = config.get(key)
    fdef = ALL_FIELDS.get(key, ("string",))
    kind = fdef[0]
    if kind == "bool":
        return "on" if raw else "off"
    if raw is None or raw == "":
        return "not set"
    if key in ("cost_alert_usd", "cost_budget_usd", "cost_hard_cap_usd"):
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return str(raw)
        if v > 0:
            return f"${v:,.2f}"
        # each zero means something different — say which
        return {
            "cost_alert_usd": "off — never warn",
            "cost_budget_usd": "off — no soft stop",
            "cost_hard_cap_usd": "off — nothing stops a runaway",
        }.get(key, "off")
    if key == "context_window":
        try:
            v = int(raw)
        except (TypeError, ValueError):
            return str(raw)
        return "auto (from the model registry)" if v <= 0 else f"{v:,} tokens"
    if key == "max_iterations":
        try:
            return f"{int(raw):,} cycles"
        except (TypeError, ValueError):
            return str(raw)
    if key in ("max_tokens_per_request", "librarian_interval", "subagent_count", "metasploit_rpc_port"):
        try:
            return f"{int(raw):,}"
        except (TypeError, ValueError):
            return str(raw)
    if key == "temperature":
        try:
            return f"{float(raw):.2f}"
        except (TypeError, ValueError):
            return str(raw)
    if key == "launch_mode":
        return "daemon (survives this console)" if str(raw) == "daemon" else "this console only"
    if key == "posture":
        return "act on its own" if str(raw) == "assertive" else "watch and report"
    if key == "zai_endpoint":
        return "coding plan (monthly)" if str(raw) == "coding" else "pay per token"
    if key == "mode_hitl":
        return "asks first" if raw else "acts on its own"
    return str(raw)


def running_summary(config: dict) -> str:
    """The one line a person actually came for: what is running, how far
    it can go, and whether anything can stop the bill. A cap of 1,000,000
    is a de-facto 'until it finishes', so it is said as that."""
    provider = human_value("provider", config)
    model = ""
    for key in (f"{provider}_model", "final_model_id", "anthropic_model", "gemini_model", "deepseek_model"):
        if key in ALL_FIELDS and config.get(key):
            model = str(config[key])
            break
    bits = [f"{provider} · {model or 'default model'}"]
    with contextlib.suppress(TypeError, ValueError):
        iters = int(config.get("max_iterations") or 0)
        bits.append("runs until it finishes" if iters >= 1_000_000 else f"stops at {iters:,} cycles")
    caps = [float(config.get(k) or 0) for k in ("cost_budget_usd", "cost_hard_cap_usd")]
    if all(c <= 0 for c in caps):
        bits.append("nothing stops the bill")
    else:
        hard = next((f"${c:,.0f}" for c in caps if c > 0), "")
        bits.append(f"bill stops at {hard}" if hard else "soft budget only")
    return " · ".join(bits)


def section_items(visible, section: str):
    """[(group, key)] for one section, in field order."""
    out = []
    for key, fdef in visible.items():
        if FIELD_INFO.get(key, (key, "", "advanced"))[2] == section:
            out.append((human_label(key), key))
    return out


def screen_lines(
    config: dict,
    visible=None,
    items=None,
    cursor: int = 0,
    note: str = "",
    status: str = "ok",
    section: str | None = None,
) -> list[tuple[str, str]]:
    """The whole screen as [(text, role)] — a PURE function of state, so it
    is asserted without a terminal.

    One flat list, every field, grouped by heading. What a person needs
    from the layout is: aligned columns, nothing cut off, and a visible
    cursor — not a second menu level.

    roles: title | head | group | cursor | row | blank | status | warn | legend
    """
    visible = visible if visible is not None else _visible_fields(config)
    items = items if items is not None else _row_items(visible)
    lines: list[tuple[str, str]] = [
        ("SUIJIN — Settings", "title"),
        (f"config: {CONFIG_PATH}", "head"),
        ("", "blank"),
    ]
    # two aligned columns: the field on the left, its value on the right.
    # A settings list where you can only see one value at a time is useless,
    # so EVERY row shows its value.
    width = max((len(k) for _g, k in items), default=10)
    for idx, (group, key) in enumerate(items):
        if idx == 0 or group != items[idx - 1][0]:
            lines.append((group, "group"))
        marker = "› " if idx == cursor else "  "
        # the key row is masked and lives in .env; everything else is a
        # plain config value
        value = api_key_state(config) if key == API_KEY_ROW else _fmt_plain(key, config.get(key, ""))
        suffix = "  · m: live ids" if is_model_field(key) else ""
        lines.append((f"{marker}{key.ljust(width)}  {value}{suffix}", "cursor" if idx == cursor else "row"))
    lines.append(("", "blank"))
    lines.append((note or visible_provider_note(config), "warn" if status == "warn" else "status"))
    return lines


def _fit(text: str, width: int) -> str:
    """Truncate with an ellipsis so a line NEVER runs off the screen."""
    width = max(1, width)
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def _legend(section: str | None = None) -> str:
    return "↑↓/jk move · Enter edit · m live model ids · s save · q save & quit"


def _fmt_plain(key: str, value) -> str:
    """Render one value for the curses screen — plain text, no Rich markup
    (markup tags printed literally on a terminal)."""
    fdef = ALL_FIELDS.get(key, ("string",))
    if fdef[0] == "bool":
        return "on" if value else "off"
    if value is None or value == "":
        return "(not set)"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e6:
        return f"{value:g}"
    if isinstance(value, (dict, list)):
        import json as _json

        return _json.dumps(value, default=str)[:60]
    return str(value)


def _render(console: Console, config: dict, visible, items, cursor: int, note: str = "") -> None:
    """Non-curses renderer (CI, piped output, and the `--dump` style paths).
    The interactive editor uses the curses screen instead — reprinting a
    table per keystroke scrolls the terminal away."""
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


# ── the curses editor ───────────────────────────────────────────────────
#
# The screen is redrawn IN PLACE (no scroll spam), a status line carries
# every outcome, and all editing happens on one input line at the bottom.
# The screen model above is pure, so the drawing here is thin and the
# whole thing is testable without a terminal.

_ROLE_ATTRS = {
    "title": ("bold",),
    "head": ("reverse",),
    "group": ("bold",),
    "cursor": ("reverse",),
    "row": (),
    "status": ("reverse",),
    "warn": ("bold",),
    "legend": ("dim",),
}


def _init_colors(stdscr=None) -> bool:
    """Real curses colors. Returns whether color is available (dumb
    terminals fall back to attributes only)."""
    import curses

    with contextlib.suppress(Exception):
        if not curses.has_colors():
            return False
        curses.start_color()
        with contextlib.suppress(Exception):
            curses.use_default_colors()  # -1 = the terminal's own bg/fg
        # A QUIET palette: the terminal's own foreground everywhere, two
        # accents (dim for chrome, red for trouble). The old mix (magenta
        # hints, green status, blue legend, colored title bar) was noise.
        # pair id → (fg, bg, attr)
        _COLOR_PAIRS.update(
            {
                1: (-1, -1, curses.A_BOLD),  # the field names
                2: (-1, -1, curses.A_BOLD),  # group headers
                3: (-1, -1, curses.A_DIM),  # status / rules
                4: (curses.COLOR_RED, -1, curses.A_BOLD),  # trouble
                5: (-1, -1, curses.A_BOLD),  # the input line (no fill — it sat ON the typing area)
                6: (-1, -1, curses.A_DIM),  # legend + hints
                7: (-1, curses.COLOR_CYAN, curses.A_BOLD),  # picker highlight
                8: (-1, -1, curses.A_BOLD),  # title
                9: (-1, -1, curses.A_DIM),  # live-ids hint
                10: (-1, -1, curses.A_DIM),  # the config path
                12: (-1, -1, curses.A_BOLD),  # the summary
                13: (-1, -1, curses.A_BOLD),  # the live model
            }
        )
        for pid, (fg, bg, attr) in list(_COLOR_PAIRS.items()):
            with contextlib.suppress(Exception):
                curses.init_pair(pid, fg, bg)
        return True
    return False


#: color pair ids (filled by _init_colors; empty → attributes only)
_COLOR_PAIRS: dict[int, tuple] = {}


def _role_attr(role: str) -> int:
    import curses

    return {
        "label": curses.A_NORMAL,
        "value": curses.A_NORMAL,
        "group": curses.A_BOLD,
        "cursor": curses.A_REVERSE,
        "status": curses.A_NORMAL,
        "warn": curses.A_BOLD,
        "legend": curses.A_DIM,
        "title": curses.A_BOLD,
        "path": curses.A_DIM,
        "hint": curses.A_BOLD,
        "input": curses.A_BOLD,
        "pick": curses.A_REVERSE,
    }.get(role, curses.A_NORMAL)


_ROLE_PAIR = {
    "summary": 12,
    "model": 13,
    "blank": 1,
    "rule": 6,
    "label": 1,
    "group": 2,
    "status": 3,
    "warn": 4,
    "input": 5,
    "legend": 6,
    "pick": 7,
    "title": 8,
    "path": 10,
    "hint": 9,
}


def _put(stdscr, y: int, x: int, text: str, role: str, width: int | None = None) -> None:
    """One styled write at (y, x). Never raises (the bottom-right cell and
    a stale layout must not kill the editor)."""
    import curses

    if not text:
        return
    limit = max(1, (width or (stdscr.getmaxyx()[1] - x)) - 1)
    line = text[:limit]
    attr = _role_attr(role)
    pair = _ROLE_PAIR.get(role)
    if pair and pair in _COLOR_PAIRS:
        attr |= curses.color_pair(pair)
    with contextlib.suppress(curses.error):
        stdscr.addstr(y, x, line, attr)


def _fit(text: str, width: int) -> str:
    """Truncate to width with an ellipsis so a line NEVER runs off the
    screen (the old legend was cut mid-word)."""
    width = max(1, width)
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def _draw(stdscr, config: dict, items, cursor: int, note: str, status: str, section: str | None = None) -> None:
    """One full repaint, IN PLACE. ONE frame, sized to the content, with
    the status line and the key legend INSIDE it — a screen with a second
    border under the first reads as broken."""
    import curses

    stdscr.erase()
    h, w = stdscr.getmaxyx()
    if h < 10 or w < 40:  # tiny window: say so instead of drawing garbage
        _put(stdscr, 0, 0, _fit("terminal too small — resize to edit settings", w - 1), "warn", w)
        stdscr.refresh()
        return

    body = screen_lines(config, items=items, cursor=cursor, note=note, status=status, section=section)
    content = [ln for ln in body if ln[0] != "" and ln[1] not in ("status", "warn")]
    status_line = next((ln for ln in body if ln[1] in ("status", "warn")), ("", "status"))
    legend = _fit(_legend(section), w - 6)

    # fit the content to the window: frame(2) + status(1) + legend(1) + a
    # spare row, so a long section scrolls and keeps the cursor visible
    max_rows = max(3, h - 6)
    room = min(len(content), max_rows)
    cursor_pos = next((i for i, (_t, role) in enumerate(content) if role == "cursor"), 0)
    top = 0
    if len(content) > room:
        top = max(0, min(cursor_pos - room // 2, len(content) - room))
    window = content[top : top + room]

    # ONE frame: top border, content, rule, status, legend, bottom border
    inner = len(window)
    box_h = inner + 5
    sub = None
    with contextlib.suppress(curses.error):
        sub = stdscr.subwin(min(box_h, h - 1), w, 0, 0)
        sub.border()
        _put(stdscr, 0, max(1, (w - len(" SUIJIN — Settings ")) // 2), " SUIJIN — Settings ", "title", w)

    for i, (text, role) in enumerate(window):
        if role == "title":
            continue  # the frame border carries it
        _put(stdscr, 1 + i, 2, _fit(text, w - 4), role, w)
    if len(content) > room:  # only when rows are actually hidden
        more = f" {top + room}/{len(content)} "
        _put(stdscr, 1 + inner, max(2, w - len(more) - 3), more, "legend", w)
    # rule + status + legend, inside the frame
    _put(stdscr, 1 + inner + 1, 2, _fit("─" * max(4, w - 6), w - 4), "rule", w)
    _put(stdscr, 1 + inner + 2, 2, _fit(status_line[0], w - 5), status_line[1], w)
    _put(stdscr, 1 + inner + 3, 2, _fit(" " + legend, w - 5), "legend", w)

    stdscr.refresh()
    with contextlib.suppress(Exception):
        if sub is not None:
            sub.delete()
    with contextlib.suppress(curses.error):
        stdscr.move(min(h - 1, 1 + inner + 3), 2)


def _draw_popup(stdscr, title: str, options: list[str], picked: int, height_cap: int) -> None:
    """A framed picker ABOVE the input line. It draws into the list area
    only and the caller repaints the frame afterwards, so nothing is left
    smeared over the table."""
    import curses

    if not options:
        return
    h, w = stdscr.getmaxyx()
    rows = max(3, min(len(options) + 2, height_cap, h - 6))
    width = min(max(len(o) for o in options) + 6, w - 4)
    top = max(1, (h - 3) - rows - 1)
    left = max(1, (w - width) // 2)
    inner = rows - 2
    first = max(0, min(picked - inner // 2, len(options) - inner))

    with contextlib.suppress(curses.error):
        sub = stdscr.subwin(rows, width, top, left)
        sub.border()
    with contextlib.suppress(curses.error):
        stdscr.addstr(top, left + 2, _fit(f" {title} ", width - 4), _role_attr("title"))
    for i, opt in enumerate(options[first : first + inner]):
        idx = first + i
        line = f" {'›' if idx == picked else ' '} {opt} "
        _put(
            stdscr,
            top + 1 + i,
            left + 1,
            _fit(line.ljust(width - 2), width - 2),
            "pick" if idx == picked else "label",
            width,
        )
    if len(options) > inner:
        _put(stdscr, top + rows - 1, left + width - 9, f" {first + 1}-{first + inner}/{len(options)} ", "legend", width)


def _prompt(
    stdscr, label: str, default: str = "", choices: list[str] | None = None, secret: bool = False
) -> str | None:
    """One input line at the bottom, with a filtering picker above it.

    A human facing 55 providers does NOT arrow through them — they type.
    So typing FILTERS the list live ("op" → openai, openrouter, opencode),
    ↑/↓ move within what is left, Enter takes the highlight, Esc cancels.

    The buffer starts EMPTY: pre-filling it with the current value made
    Enter return that same value, so arrowing and pressing Enter appeared
    to do nothing at all.
    """
    import curses

    opts_all = list(choices or [])
    typed = ""
    picked = 0
    h, w = stdscr.getmaxyx()
    stdscr.keypad(True)
    repaint = getattr(stdscr, "_suijin_repaint", None)

    def matches() -> list[str]:
        if not typed:
            return list(opts_all)
        low = typed.lower()
        return [o for o in opts_all if low in o.lower()]

    def reset_highlight(rows: list[str]) -> None:
        """Keep the current value under the cursor when it still matches."""
        nonlocal picked
        picked = rows.index(default) if (default and default in rows) else 0

    while True:
        rows = matches()
        if not rows:
            rows = [""]
        picked = max(0, min(picked, len(rows) - 1))
        if opts_all:
            hint = f"matching {len(rows)} of {len(opts_all)}" if typed else f"{len(opts_all)} options · type to filter"
            _draw_popup(stdscr, f"{label} — {hint}", rows, picked, max(5, h - 8))
        # ONE quiet input line: the label, then what you typed, on the
        # terminal's own background. (It used to be a full-width cyan BAR
        # across the bottom row — the "blue strip" over the typing area.)
        # The caret sits exactly where the text ends.
        _put(stdscr, h - 1, 0, " " * max(1, w - 1), "input", w)
        # a secret is NEVER echoed — only a dot per character
        shown = ("*" * len(typed)) if secret else typed
        text = f"{label}: {shown}"
        _put(stdscr, h - 1, 1, _fit(text, w - 2), "input", w)
        stdscr.refresh()
        with contextlib.suppress(curses.error):
            stdscr.move(h - 1, min(w - 2, 1 + len(text)))

        ch = stdscr.getch()
        if ch in (curses.KEY_ENTER, 10, 13):
            if not opts_all:  # free text
                value = typed.strip()
                if repaint:
                    repaint()
                return value or None
            if not rows or rows == [""]:
                continue
            choice = rows[picked]
            if repaint:
                repaint()
            return choice
        if ch == 27:  # Esc cancels
            if repaint:
                repaint()
            return None
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            if typed:
                typed = typed[:-1]
                reset_highlight(matches())
        elif opts_all and ch in (curses.KEY_UP, ord("k")):
            picked = max(0, picked - 1)
        elif opts_all and ch in (curses.KEY_DOWN, ord("j")):
            picked = min(len(rows) - 1, picked + 1)
        elif 32 <= ch < 127:
            typed += chr(ch)
            picked = 0
        elif repaint and ch == curses.KEY_RESIZE:
            repaint()


def edit_api_key_curses(stdscr, config: dict) -> tuple[str, bool]:
    """Enter a provider API key. MASKED while typing, written to the .env
    file by the provider layer, and never displayed again — the row only
    ever says whether a key is on file."""
    from suijin.modules.providers.lib import set_provider_key

    provider = str(config.get("provider") or "")
    raw = _prompt(stdscr, f"{provider} API key (hidden)", default="", secret=True)
    if raw is None:
        return "", False
    ok, message = set_provider_key(provider, raw)
    return (message if ok else f"key not saved — {message}"), ok


def edit_field_curses(stdscr, config: dict, key: str) -> tuple[str, bool]:
    """Edit one field on the curses screen. Returns (note, changed)."""
    if key == API_KEY_ROW:
        return edit_api_key_curses(stdscr, config)
    fdef = ALL_FIELDS.get(key, ("string",))
    kind = fdef[0]
    current = str(config.get(key, "") or "")
    try:
        if kind == "bool":
            # A checkbox flips on Enter. It must never open a prompt where
            # Enter means "cancel" — that made every bool un-togglable.
            flipped = "off" if config.get(key) else "on"
            changed = _apply(config, key, flipped)
            return f"{key}: {flipped}" + ("" if changed else "  (unchanged)"), changed
        if kind == "provider":
            choices = provider_choices(config)
            if current and current not in choices:
                choices = [current] + choices
            raw = _prompt(stdscr, f"provider ({len(choices)})", default=current, choices=choices)
            if raw is None:
                return "", False
            return _commit(config, key, raw, f"{key} = {raw}")
        if kind == "choice":
            choices = list(fdef[1] or [])
            if current and current not in choices:
                choices = [current] + choices
            raw = _prompt(stdscr, key, default=current, choices=choices)
            if raw is None:
                return "", False
            return _commit(config, key, raw, f"{key} = {raw}")
        if kind == "model":
            hint = _default_model_hint(str(config.get("provider") or ""))
            label = f"{key} (m=fetch live ids{'; default ' + hint if hint else ''})"
            raw = _prompt(stdscr, label, default=current or hint)
            if raw is None:
                return "", False
            return _commit(config, key, raw, f"{key} = {raw}")
        raw = _prompt(stdscr, f"{key} [{_fmt_plain(key, current)}]", default=current)
        if raw is None:
            return "", False
        return _commit(config, key, raw, f"{key} = {raw}")
    except (ValueError, TypeError):
        return f"{key}: value rejected — keeping the current one", False


def _commit(config: dict, key: str, raw: str, note: str) -> tuple[str, bool]:
    changed = _apply(config, key, raw)
    return note + ("  (changed)" if changed else "  (unchanged)"), changed


def pick_model_curses(stdscr, config: dict, key: str) -> str:
    """Fetch the provider's LIVE model ids and pick one, on-screen."""
    if not is_model_field(key):
        return f"{key} is not a model field (Enter to type an id)"
    provider = str(config.get("provider") or "")
    if not provider:
        return "no provider selected — set `provider` first"
    ids, note = fetch_model_ids(provider, config)
    if not ids:
        return note
    shown = ids[:MODEL_LIST_CAP]
    current = str(config.get(key, "") or "")
    if current and current not in shown:
        shown = [current] + shown
    raw = _prompt(stdscr, f"{provider} models ({len(ids)}) — Enter picks, Esc cancels", default="", choices=shown)
    if raw is None:
        return note
    changed = _apply(config, key, raw)
    return f"{key} = {raw}" + ("  (changed)" if changed else "  (unchanged)") + f" — {note}"


def _apply(config: dict, key: str, raw: str) -> bool:
    """Parse+clamp one raw value into config. True when it changed.

    The ONLY writer in the editor: a value changes here or not at all, so
    navigation can never touch config."""
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


def _curses_loop(stdscr) -> int:
    import curses

    config, status = load_state()
    if status == "corrupt":
        _put(
            stdscr,
            0,
            0,
            _fit(f"config.json is CORRUPT — refusing to overwrite. Fix it by hand: {CONFIG_PATH}", 120),
            "warn",
        )
        stdscr.refresh()
        stdscr.getch()  # any key dismisses
        return 1
    with contextlib.suppress(Exception):
        # the .env is loaded ONCE here, so the key row shows the real
        # state and a key just written is visible
        from suijin.modules.platform.lib.config_loader import load_env

        load_env()
    _init_colors(stdscr)
    stdscr.keypad(True)
    cursor = 0
    note = ""
    loop_status = "ok"

    def _repaint() -> None:
        _draw(stdscr, config, None, cursor, note, loop_status)

    with contextlib.suppress(Exception):
        stdscr._suijin_repaint = _repaint

    while True:
        items = _row_items(_visible_fields(config))
        if not items:
            return 0
        cursor = max(0, min(cursor, len(items) - 1))
        _repaint()
        note, loop_status = "", "ok"
        ch = stdscr.getch()
        key = items[cursor][1]

        if ch in (ord("q"), ord("Q"), 27):  # q / Esc leave (after saving)
            _save_line_quiet(config, status)
            return 0
        if ch in (curses.KEY_UP, ord("k")):
            cursor = (cursor - 1) % len(items)
        elif ch in (curses.KEY_DOWN, ord("j")):
            cursor = (cursor + 1) % len(items)
        elif ch in (curses.KEY_ENTER, 10, 13, ord("e")):  # edit this field
            note, _ = edit_field_curses(stdscr, config, key)
        elif ch in (ord("m"), ord("M")):  # fetch the live model ids
            note = pick_model_curses(stdscr, config, key)
        elif ch in (ord("s"), ord("S")):
            ok = _save_line(stdscr, config, status)
            note, loop_status = ("saved — " + CONFIG_PATH, "ok") if ok else (f"save failed — {CONFIG_PATH}", "warn")


def _save_line_quiet(config: dict, status: str) -> bool:
    """q saves and quits (the operator never loses an edit they made)."""
    return _save_line(None, config, status)


def _save_line(stdscr, config: dict, status: str) -> bool:
    """Save from the curses screen. True = saved.

    This used to return an exit CODE (0 = ok) while the caller read it as
    a truthy "ok", so every single save reported "save failed" even though
    the file was written. It returns a bool now."""
    if status == "corrupt":
        return False
    try:
        save_config(config)
    except Exception as e:  # noqa: BLE001
        print(f"save error: {e}", file=sys.stderr)
        return False
    return True


def run_editor(console: Console) -> int:
    """Launch the curses editor. Falls back to the Rich print path when
    curses cannot start (a dumb terminal, a CI pty) — Settings always
    opens, never tracebacks."""
    import curses

    try:
        return int(curses.wrapper(_curses_loop) or 0)
    except Exception:  # noqa: BLE001 — no TERM, no terminfo, a broken pty
        # line-mode fallback (also the off-TTY test path)
        return _run_line_mode(console)


def _run_line_mode(console: Console) -> int:
    """The no-curses fallback: a printed table + a command line. Used by CI
    and by terminals curses can't drive."""
    config, status = load_state()
    cursor = 0
    note = ""
    while True:
        visible = _visible_fields(config)
        items = _row_items(visible)
        cursor = max(0, min(cursor, len(items) - 1))
        _render(console, config, visible, items, cursor, note)
        note = ""
        try:
            line = console.input("[dim]n next · e <field> <value> · m <field> · s save · q quit[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        low = line.lower()
        if low in ("q", "quit", ""):
            return 0
        if low == "s":
            return _save(console, config, status)
        if low.startswith("e "):
            parts = (line.split(" ", 2) + ["", ""])[:3]
            try:
                _apply(config, parts[1].strip(), parts[2])
            except Exception as e:  # noqa: BLE001
                note = f"{parts[1]}: {e}"
        elif low.startswith("m "):
            key = line.split(" ", 1)[1].strip()
            ids, fetch_note = fetch_model_ids(str(config.get("provider") or ""), config)
            if not ids:
                note = fetch_note
            else:
                try:
                    raw = Prompt.ask(
                        f"{config.get('provider')} models ({len(ids)})", console=console, choices=ids[:MODEL_LIST_CAP]
                    )
                except (EOFError, KeyboardInterrupt):
                    raw = None
                if raw:
                    _apply(config, key, raw)
                    note = f"{key} = {raw}  (changed) — {fetch_note}"
        elif low == "n":
            cursor += 1
        else:
            note = "unknown command — n / e <field> <value> / m <field> / s / q"
        if low == "n" and items:
            pass
    return 0


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
