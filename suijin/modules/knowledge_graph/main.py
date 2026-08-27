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


def check_knowledge(target, payload=None):
    if not target:
        return "Error: target required"
    return _get_tools().check_knowledge(target, payload=payload)


def record_finding(target, finding_type, rule, evidence=""):
    if not target or not finding_type or not rule:
        return "Error: target, finding_type, and rule required"
    return _get_tools().record_finding(target, finding_type, rule, evidence=evidence)
