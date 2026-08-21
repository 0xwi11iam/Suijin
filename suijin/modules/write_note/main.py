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
            os.path.join(
                os.path.dirname(__file__), "..", "..", "..", _stealth_ua(), "modules", "tools", "lib", "dispatch.py"
            )
        )
        spec = importlib.util.spec_from_file_location("tools_core3", p)
        _tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_tools)
    return _tools


def write_note(content="", success=True, category="general", engagement=None):
    if not content:
        return "Error: content required"
    return _get_tools().write_note(content, success=success, category=category, engagement=engagement)
