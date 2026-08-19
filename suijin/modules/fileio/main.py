import importlib.util, os

_tools = None


def _get_tools():
    global _tools
    if _tools is None:
        p = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "suijin", "tools", "dispatch.py"))
        spec = importlib.util.spec_from_file_location("tools_core4_io", p)
        _tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_tools)
    return _tools


def read_file(file_path=""):
    if not file_path:
        return "Error: file_path required"
    return _get_tools().read_file(file_path)


def write_file(file_path="", content=""):
    if not file_path:
        return "Error: file_path required"
    return _get_tools().write_file(file_path, content)
