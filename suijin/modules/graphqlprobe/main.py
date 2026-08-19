import requests

_T = (5, 20)
_UA = {"User-Agent": "Mozilla/5.0 (suijin recon)"}


def _get(url, **kw):
    return requests.get(url, timeout=_T, headers=_UA, **kw)


_INTROSPECTION = {"query": "{ __schema { types { name fields { name } } queryType { name } mutationType { name } } }"}


def graphql_introspect(url: str = "") -> str:
    if not url:
        return "Error: url required"
    try:
        r = requests.post(
            url.strip(), json=_INTROSPECTION, timeout=_T, headers={**_UA, "Content-Type": "application/json"}
        )
    except requests.RequestException as e:
        return f"Error: {e}"
    if r.status_code != 200:
        return f"endpoint returned {r.status_code} (introspection may be blocked)"
    try:
        data = r.json().get("data") or {}
    except ValueError:
        return "non-JSON response (not GraphQL or heavily proxied)"
    schema = data.get("__schema")
    if not schema:
        errors = r.json().get("errors")
        return "introspection disabled" + (f" ({str(errors)[:150]})" if errors else "")
    types = schema.get("types") or []
    interesting = [t.get("name") for t in types if t.get("name") and not t["name"].startswith("__")]
    fields = {
        t["name"]: [f["name"] for f in (t.get("fields") or [])][:8]
        for t in types
        if t.get("fields") and t["name"] in interesting[:12]
    }
    out = [
        f"INTROSPECTION ALLOWED: {len(types)} types, query={schema.get('queryType')}, mutation={schema.get('mutationType')}"
    ]
    for tn, fl in list(fields.items())[:12]:
        out.append(f"  {tn}: {', '.join(fl)}")
    return "\n".join(out)


def graphql_suggest(url: str = "", field: str = "usre") -> str:
    if not url:
        return "Error: url required"
    probe = field or "usre"
    q = {"query": f"{{ {probe} {{ id }} }}"}
    try:
        r = requests.post(url.strip(), json=q, timeout=_T, headers={**_UA, "Content-Type": "application/json"})
    except requests.RequestException as e:
        return f"Error: {e}"
    try:
        body = r.json()
    except ValueError:
        return f"non-JSON ({r.status_code})"
    msgs = [e.get("message", "") for e in body.get("errors") or []]
    suggests = [m for m in msgs if "Did you mean" in m]
    if suggests:
        return "SCHEMA LEAK via suggestions:\n  " + "\n  ".join(suggests[:10])
    return f"no suggestions leaked ({msgs[:1] or 'silent'})"
