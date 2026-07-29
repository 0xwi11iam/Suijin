import importlib.util, os
_tools = None
def _get_tools():
    global _tools
    if _tools is None:
        p = os.path.abspath(os.path.join(os.path.dirname(__file__),"..","..","..","medusa","tools","dispatch.py"))
        spec = importlib.util.spec_from_file_location("tools_cve", p)
        _tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_tools)
    return _tools

def search_cve(software, version=None, limit=5):
    if not software: return "Error: software required"
    import json
    with open(os.path.abspath(os.path.join(os.path.dirname(__file__),"..","..","..","medusa","config.json"))) as f:
        cfg = json.load(f)
    return _get_tools().search_cve(software, cfg, version=version, limit=int(limit or 5))
