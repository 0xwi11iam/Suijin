import importlib.util, os
_tools = None
def _get_tools():
    global _tools
    if _tools is None:
        p = os.path.abspath(os.path.join(os.path.dirname(__file__),"..","..","..","medusa","tools","dispatch.py"))
        spec = importlib.util.spec_from_file_location("tools_core_utils", p)
        _tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_tools)
    return _tools

def search_kb(keyword):
    if not keyword: return "Error: keyword required"
    return _get_tools().search_kb(keyword)

def apply_patch(vulnerability, file_path="lab.py"):
    if not vulnerability: return "Error: vulnerability required"
    return _get_tools().apply_patch(vulnerability, file_path)

def claim_flag(flag):
    if not flag: return "Error: flag required"
    return f"OBJECTIVE MET: {flag}"
