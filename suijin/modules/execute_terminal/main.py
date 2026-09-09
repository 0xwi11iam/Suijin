"""Shell execution — delegates to core tools.execute_terminal."""

import importlib.util, os


def _stealth_ua() -> str:
    try:
        from suijin.modules.platform.lib.stealth import user_agent

        return user_agent()
    except Exception:  # standalone fallback
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


_tools = None


def _get_tools():
    global _tools
    if _tools is None:
        # CANONICAL import — the old spec_from_file_location force-load
        # created a SECOND dispatch module instance (independent
        # repeat-guard ledgers, drift hazard) shadowing core routes
        from suijin.modules.tools.lib import dispatch as _dispatch_mod

        _tools = _dispatch_mod
    return _tools


def execute_terminal(cmd=None, command=None, timeout=30):
    actual_cmd = cmd or command
    if not actual_cmd:
        return "Error: cmd required"
    return _get_tools().execute_terminal(actual_cmd, timeout=int(timeout))
