"""Suijin - Autonomous Cyber Reasoning System"""

import json as _json
from pathlib import Path as _Path

# Single source of truth for the version: suijin/version.json
__version__ = _json.loads((_Path(__file__).parent / "version.json").read_text())["version"]
