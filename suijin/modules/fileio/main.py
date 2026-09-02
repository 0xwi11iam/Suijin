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
        from suijin.modules.tools.lib import dispatch as _dispatch_mod

        _tools = _dispatch_mod
    return _tools


def read_file(file_path=""):
    if not file_path:
        return "Error: file_path required"
    return _get_tools().read_file(file_path)


def write_file(file_path="", content=""):
    if not file_path:
        return "Error: file_path required"
    return _get_tools().write_file(file_path, content)
