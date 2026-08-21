import requests


def _stealth_ua() -> str:
    try:
        from suijin.modules.platform.lib.stealth import user_agent

        return user_agent()
    except Exception:  # standalone fallback
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


_T = (5, 20)
_UA = {"User-Agent": "_stealth_ua()"}


def _get(url, **kw):
    return requests.get(url, timeout=_T, headers=_UA, **kw)


_PATHS = (
    "/swagger.json",
    "/openapi.json",
    "/api-docs",
    "/v2/swagger.json",
    "/v3/api-docs",
    "/api/swagger.json",
    "/api/openapi.json",
    "/swagger/v1/swagger.json",
)


def openapi_find(url: str = "") -> str:
    if not url:
        return "Error: url required"
    base = url.strip().rstrip("/")
    if "://" not in base:
        base = "http://" + base
    hits = []
    for p in _PATHS:
        try:
            r = _get(base + p)
            if r.status_code == 200 and ("openapi" in r.text[:600] or "swagger" in r.text[:600]):
                hits.append(base + p)
        except requests.RequestException:
            continue
    return "\n".join(hits) if hits else "No OpenAPI specs found at common paths."


def openapi_parse(spec: str = "", base_url: str = "") -> str:
    if not spec:
        return "Error: spec text required (fetch with openapi_find or http_request)"
    import json

    try:
        doc = json.loads(spec)
    except ValueError:
        try:
            import yaml

            doc = yaml.safe_load(spec)
        except Exception:
            return "Error: not valid JSON or YAML"
    base = base_url.strip().rstrip("/") if base_url else ""
    paths = doc.get("paths") or {}
    out = [
        f"{len(paths)} paths, title={doc.get('info', {}).get('title', '?')} v{doc.get('info', {}).get('version', '?')}"
    ]
    risky = []
    for path, ops in sorted(paths.items()):
        methods = [m.upper() for m in ops if m in ("get", "post", "put", "delete", "patch")]
        params = [p.get("name") for p in (ops.get("parameters") or [])][:6]
        line = f"  {','.join(methods):14} {path}" + (f"  params={params}" if params else "")
        out.append(line)
        if any(
            x in path.lower() for x in ("admin", "debug", "internal", "token", "secret", "user", "upload", "export")
        ):
            risky.append(path)
    if risky:
        out.append("high-value paths:\n  " + "\n  ".join(risky))
    if base:
        out.append("first absolute targets:\n  " + "\n  ".join(f"{base}{p}" for p in list(paths)[:10]))
    return "\n".join(out)
