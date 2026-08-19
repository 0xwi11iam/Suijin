import importlib.util, os
_tools = None
def _get_tools():
    global _tools
    if _tools is None:
        p = os.path.abspath(os.path.join(os.path.dirname(__file__),"..","..","..","suijin","modules", "tools", "lib","dispatch.py"))
        spec = importlib.util.spec_from_file_location("tools_core3", p)
        _tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_tools)
    return _tools

def write_note(content="", success=True, category="general", engagement=None):
    if not content: return "Error: content required"
    return _get_tools().write_note(content, success=success, category=category, engagement=engagement)
