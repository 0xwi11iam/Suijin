"""Suijin - Autonomous Cyber Reasoning System"""

import json as _json

# Single source of truth for the version: suijin/version.json
# importlib.resources works from a normal install AND from inside a zipapp
# (where Path(__file__).parent is not a real directory).
try:
    from importlib.resources import files as _files

    __version__ = _json.loads(_files("suijin").joinpath("version.json").read_text())["version"]
except Exception:  # noqa: BLE001 — frozen/zip exotic fallback
    __version__ = "0.0.0"
