"""OpenAPI/Swagger parser — parse API specs, generate test plan."""
import json, urllib.request

def openapi_parse(url_or_path):
    """Parse an OpenAPI/Swagger spec from URL or file path. Returns endpoint summary."""
    try:
        if url_or_path.startswith("http"):
            with urllib.request.urlopen(url_or_path) as r:
                spec = json.loads(r.read())
        else:
            with open(url_or_path) as f:
                spec = json.load(f)
    except Exception as e: return f"Parse error: {e}"
    endpoints = []
    base_url = spec.get("servers", [{}])[0].get("url", "") if "servers" in spec else ""
    for path, methods in spec.get("paths", {}).items():
        for method, details in methods.items():
            if method.upper() in ("GET","POST","PUT","DELETE","PATCH"):
                params = [p["name"] for p in details.get("parameters",[]) if p.get("name")]
                endpoints.append(f"  {method.upper():7s} {base_url}{path}  params={params}")
    return f"Found {len(endpoints)} endpoints:\n" + "\n".join(endpoints[:40])
