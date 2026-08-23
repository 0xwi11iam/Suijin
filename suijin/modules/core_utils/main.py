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
                os.path.dirname(__file__), "..", "..", "..", "suijin", "modules", "tools", "lib", "dispatch.py"
            )
        )
        spec = importlib.util.spec_from_file_location("tools_core_utils", p)
        _tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_tools)
    return _tools


def search_kb(keyword, limit=5):
    """Core forwarding shim — accepts the SAME signature the prompt
    registry documents (keyword + limit). The pack loader registers this
    in the flat tool namespace, shadowing the core search_kb; a narrower
    signature here made every documented call with limit= throw."""
    if not keyword:
        return "Error: keyword required"
    return _get_tools().search_kb(keyword, limit=limit)


def apply_patch(vulnerability, file_path="lab.py"):
    if not vulnerability:
        return "Error: vulnerability required"
    return _get_tools().apply_patch(vulnerability, file_path)


def claim_flag(flag):
    if not flag:
        return "Error: flag required"
    return f"OBJECTIVE MET: {flag}"
