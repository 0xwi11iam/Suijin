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


def search_cve(software, version=None, limit=5):
    if not software:
        return "Error: software required"
    import json

    with open(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "suijin", "config.json"))) as f:
        cfg = json.load(f)
    return _get_tools().search_cve(software, cfg, version=version, limit=int(limit or 5))
