"""HAR file replay — load HTTP Archive and replay requests."""
import json
from pathlib import Path

def har_parse(filepath: str) -> list:
    try:
        har = json.loads(Path(filepath).read_text())
        entries = har.get("log", {}).get("entries", [])
        requests = []
        for e in entries[:50]:
            req = e.get("request", {})
            requests.append({"method": req.get("method","GET"), "url": req.get("url",""),
                             "headers": {h["name"]: h["value"] for h in req.get("headers",[])},
                             "body": req.get("postData",{}).get("text","")})
        return requests
    except Exception as e: return [{"error": str(e)}]

def har_replay(har_path: str, base_url_override: str = "") -> list:
    requests = har_parse(har_path)
    if not requests or "error" in requests[0]: return requests
    results = []
    for r in requests[:20]:
        url = r["url"] if not base_url_override else base_url_override + r["url"].split("/",3)[-1] if "/" in r["url"] else base_url_override
        results.append({"method": r["method"], "url": url, "headers": r["headers"], "body": r["body"][:200]})
    return results
