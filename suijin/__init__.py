"""Suijin - Autonomous Cyber Reasoning System"""

import json as _json
import warnings as _warnings

# Third-party deprecation noise (langgraph serializer advisory) — must live
# HERE, at package init: every entrypoint imports `suijin` before anything
# can pull langgraph in, so this filter always wins the race. The filters
# that lived in main.py/cli.py installed too late when langgraph was
# imported at module level first.
_warnings.filterwarnings("ignore", message=".*allowed_objects.*")

# Single source of truth for the version: suijin/version.json
# importlib.resources works from a normal install AND from inside a zipapp
# (where Path(__file__).parent is not a real directory).
try:
    from importlib.resources import files as _files

    __version__ = _json.loads(_files("suijin").joinpath("version.json").read_text())["version"]
except Exception:  # noqa: BLE001 — frozen/zip exotic fallback
    __version__ = "0.0.0"
