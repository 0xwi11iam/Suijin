"""Local notification hooks — offline, no external services.

Channels configured in medusa/notify.json:
  {"macos": true,                 # osascript notification
   "command": "say {message}",    # arbitrary command, {message} substituted
   "file": "/path/to/log"}        # append "[iso] title: message"

CLI: medusa notify send "msg" / medusa notify test. Battle mode fires
notify on flag captures and network blocks when configured.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # medusa/
CONFIG_PATH = BASE_DIR / "notify.json"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def send(title: str, message: str, config: dict | None = None) -> list[str]:
    """Dispatch one notification to every configured channel. Returns log."""
    cfg = config if config is not None else load_config()
    results: list[str] = []
    if not cfg:
        return ["notify: no channels configured (medusa/notify.json)"]
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
    CONFIG_PATH.write_text(
        json.dumps(
            {"macos": False, "command": "", "file": str(BASE_DIR.parent / "medusa_agent" / "notifications.log")},
            indent=2,
        )
    )
    return f"example config written to {CONFIG_PATH} — edit channels, then 'medusa notify test'"
