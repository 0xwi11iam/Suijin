"""Local notification hooks — offline, no external services.

Channels configured in suijin/notify.json:
  {"macos": true,                 # osascript notification
   "command": "say {message}",    # arbitrary command, {message} substituted
   "file": "/path/to/log"}        # append "[iso] title: message"

CLI: suijin notify send "msg" / suijin notify test. Battle mode fires
notify on flag captures and network blocks when configured.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]  # suijin/ package
# v4.1: operator config — lives in the workspace (the volume). Lazy
# accessor (boundary rule) honouring a monkeypatched module attr.
CONFIG_PATH = None


def _config_path():
    v = globals().get("CONFIG_PATH")
    if v is not None:
        return v  # monkeypatched / set by the operator
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR / "notify.json"


def __getattr__(name):
    if name == "CONFIG_PATH":
        return _config_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def load_config() -> dict:
    if not _config_path().exists():
        return {}
    try:
        data = json.loads(_config_path().read_text())
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def send(title: str, message: str, config: dict | None = None) -> list[str]:
    """Dispatch one notification to every configured channel. Returns log."""
    cfg = config if config is not None else load_config()
    results: list[str] = []
    if not cfg:
        return ["notify: no channels configured (suijin/notify.json)"]
    if cfg.get("macos"):
        try:
            safe_title = title.replace('"', "'")
            safe_msg = message.replace('"', "'")
            subprocess.run(
                ["osascript", "-e", f'display notification "{safe_msg}" with title "{safe_title}"'],
                timeout=5,
                check=False,
            )
            results.append("macos: sent")
        except (OSError, subprocess.SubprocessError) as e:
            results.append(f"macos: failed — {e}")
    if cfg.get("command"):
        try:
            cmd = cfg["command"].replace("{message}", message.replace("'", "'\\''"))
            subprocess.run(shlex.split(cmd), timeout=15, check=False)
            results.append("command: sent")
        except (OSError, subprocess.SubprocessError) as e:
            results.append(f"command: failed — {e}")
    if cfg.get("file"):
        try:
            p = Path(cfg["file"]).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a") as f:
                f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {title}: {message}\n")
            results.append(f"file: appended to {p}")
        except OSError as e:
            results.append(f"file: failed — {e}")
    return results


def write_example_config() -> str:
    _config_path().write_text(
        json.dumps(
            {"macos": False, "command": "", "file": str(BASE_DIR.parent / "suijin_agent" / "notifications.log")},
            indent=2,
        )
    )
    return f"example config written to {_config_path()} — edit channels, then 'suijin notify test'"
