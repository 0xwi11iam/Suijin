"""GraphQL Schema Diff — compare introspection results across users/roles to find IDOR."""

import json, urllib.request


def graphql_introspect(url: str, headers: str = "{}") -> str:
    query = '{"query":"{__schema{types{name fields{name} enumValues{name}}}}"}'
    try:
        hdrs = json.loads(headers) if headers else {"Content-Type": "application/json"}
        req = urllib.request.Request(url, data=query.encode(), headers=hdrs)
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.read().decode()[:5000]
    except Exception as e:
        return f"Introspection error: {e}"


def graphql_diff(url: str, header_set1: str, header_set2: str) -> str:
    r1 = graphql_introspect(url, header_set1)
    r2 = graphql_introspect(url, header_set2)
    if r1 == r2:
        return "No difference — both users see the same schema."
    return f"SCHEMA DIFFERENCE DETECTED (potential IDOR):\nUser 1 sees {len(r1)} bytes\nUser 2 sees {len(r2)} bytes\n1: {r1[:300]}\n2: {r2[:300]}"
