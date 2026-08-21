import requests


def _stealth_ua() -> str:
    try:
        from suijin.modules.platform.lib.stealth import user_agent

        return user_agent()
    except Exception:  # standalone fallback
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


_T = (5, 20)
_UA = {"User-Agent": _stealth_ua()}


def _get(url, **kw):
    return requests.get(url, timeout=_T, headers=_UA, **kw)


def grpc_detect(host: str = "", port: int = 50051) -> str:
    if not host:
        return "Error: host required"
    h = host.strip()
    out = []
    # grpc reflection probe (POST /grpc.reflection.v1alpha.ServerReflection/Info over h2)
    try:
        r = requests.post(
            f"http://{h}:{int(port or 50051)}/grpc.reflection.v1alpha.ServerReflection/Info",
            data=b"",
            timeout=(3, 8),
            headers={**_UA, "Content-Type": "application/grpc", "TE": "trailers"},
        )
        ct = r.headers.get("content-type", "")
        if "grpc" in ct:
            out.append(f"gRPC reflection endpoint ACTIVE on :{int(port or 50051)} (content-type={ct})")
        else:
            out.append(f"HTTP response {r.status_code} ct={ct or '-'} (not gRPC over h1)")
    except requests.RequestException as e:
        out.append(f"h1 probe failed: {type(e).__name__}")
    # grpc-web
    try:
        r2 = requests.options(f"http://{h}:{int(port or 50051)}/", timeout=(3, 8), headers=_UA)
        if "grpc" in (r2.headers.get("access-control-allow-headers", "") or ""):
            out.append("grpc-web CORS headers present — browser-reachable gRPC")
    except requests.RequestException:
        pass
    return "\n".join(out) + "\nPair with grpcurl (execute_terminal) for reflection dumps."
