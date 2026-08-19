"""Shell execution — delegates to core tools.execute_terminal."""
import importlib.util, os
_tools = None
def _get_tools():
    global _tools
    if _tools is None:
        p = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "suijin", "modules", "tools", "lib", "dispatch.py"))
        spec = importlib.util.spec_from_file_location("tools_core", p)
        _tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_tools)
    return _tools

def execute_terminal(cmd=None, command=None, timeout=30):
    actual_cmd = cmd or command
    if not actual_cmd: return "Error: cmd required"
    return _get_tools().execute_terminal(actual_cmd, timeout=int(timeout))
