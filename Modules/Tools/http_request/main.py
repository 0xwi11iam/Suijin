import importlib.util, os, json
_tools = None
def _get_tools():
    global _tools
    if _tools is None:
        p = os.path.abspath(os.path.join(os.path.dirname(__file__),"..","..","..","medusa","tools","dispatch.py"))
        spec = importlib.util.spec_from_file_location("tools_dispatch", p)
        _tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_tools)
    return _tools

def http_request(method="GET", url="", headers=None, body=""):
    if not url: return "Error: url required"
    if headers and isinstance(headers, str):
        try: headers = json.loads(headers)
        except: pass
    return _get_tools().http_request(method, url, headers, body)
