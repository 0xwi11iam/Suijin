import json
import shlex
import subprocess
from pathlib import Path


def _store():
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    p = WORKSPACE_DIR / "custom_commands.json"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    try:
        return p, json.loads(p.read_text())
    except ValueError:
        return p, {}


def _save(p, data):
    p.write_text(json.dumps(data, indent=2))


def custom_cmd_define(name: str = "", command: str = "") -> str:
    if not name or not command:
        return "Error: name and command required"
    if "/" in name or name.startswith("."):
        return "Error: name must be a plain identifier"
    p, data = _store()
    data[name.strip()] = command.strip()
    _save(p, data)
    return f"defined '{name}': {command.strip()}"


def custom_cmd_list() -> str:
    _p, data = _store()
    if not data:
        return "No custom commands defined. Use custom_cmd_define."
    return "\n".join(f"{k}: {v}" for k, v in sorted(data.items()))


def custom_cmd_delete(name: str = "") -> str:
    if not name:
        return "Error: name required"
    p, data = _store()
    if name.strip() not in data:
        return f"Error: no command named '{name}'"
    del data[name.strip()]
    _save(p, data)
    return f"deleted '{name}'"


def custom_cmd_run(name: str = "", args: str = "") -> str:
    if not name:
        return "Error: name required"
    _p, data = _store()
    tpl = data.get(name.strip())
    if tpl is None:
        return f"Error: no command named '{name}' (custom_cmd_define it first)"
    vals = {}
    for part in (args or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            vals[k.strip()] = v.strip()
    try:
        rendered = tpl.format(**vals)
    except KeyError as e:
        return f"Error: missing arg {e} (provide as {str(e)[1:-1]}=value)"
    # guardrails: same dangerous-command gate as execute_terminal
    from suijin.modules.tools.lib.guardrails import is_dangerous

    dangerous, pattern = is_dangerous(rendered)
    if dangerous:
        return f"Command denied by guardrail (matched: {pattern})."
    try:
        argv = shlex.split(rendered)
    except ValueError as e:
        return f"Error: unbalanced quotes: {e}"
    if not argv:
        return "Error: rendered command is empty"
    try:
        try:  # Stealth (v5.1): loud tools rate-capped, benign untouched
            from suijin.modules.platform.lib.stealth import sanitize_command

            argv = sanitize_command(argv)
        except Exception:  # noqa: BLE001 — never block a custom command
            pass
        r = subprocess.run(argv, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        return f"Error: '{argv[0]}' not installed"
    except subprocess.TimeoutExpired:
        return "Error: timed out after 180s"
    out = (r.stdout or "") + (f"\n[stderr]\n{r.stderr}" if r.stderr else "")
    return f"exit={r.returncode}\n{out[:8000] or '(no output)'}"
