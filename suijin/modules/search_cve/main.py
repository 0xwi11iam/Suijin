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
        p = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", _stealth_ua(), "tools", "dispatch.py")
        )
        spec = importlib.util.spec_from_file_location("tools_cve", p)
        _tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_tools)
    return _tools


def search_cve(software, version=None, limit=5):
    if not software:
        return "Error: software required"
    import json

    with open(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", _stealth_ua(), "config.json"))
    ) as f:
        cfg = json.load(f)
    return _get_tools().search_cve(software, cfg, version=version, limit=int(limit or 5))
