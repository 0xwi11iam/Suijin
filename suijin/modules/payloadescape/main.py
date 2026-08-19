import urllib.parse


def encode_payload_context(payload: str = "", context: str = "url") -> str:
    if not payload:
        return "Error: payload required"
    ctx = (context or "url").lower()
    tables = {
        "html": {"<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "&": "&amp;"},
        "html_attr": {'"': "&quot;", "'": "&#39;", "<": "&lt;", ">": "&gt;"},
        "js": {"\\": "\\\\", "'": "\\'", '"': '\\"', "\n": "\\n", "</": "<\\/"},
        "sql": {"'": "''", "\\": "\\\\"},
        "ldap": {"*": "\\2a", "(": "\\28", ")": "\\29", "\\": "\\5c", "NUL": "\\00"},
    }
    if ctx in tables:
        t = tables[ctx]
        out = "".join(t.get(c, c) for c in payload)
        return f"[{ctx}] {out}"
    if ctx == "url":
        return f"[url] {urllib.parse.quote(payload, safe='')}"
    if ctx == "url_double":
        once = urllib.parse.quote(payload, safe="")
        return f"[url_double] {urllib.parse.quote(once, safe='')}"
    return f"Error: unknown context {ctx!r} (html|html_attr|js|url|url_double|sql|ldap)"
