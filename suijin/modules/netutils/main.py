import socket
import time


def tcp_ping(host: str = "", port: int = 0, count: int = 3) -> str:
    if not host or not port:
        return "Error: host and port required"
    n = max(1, min(int(count or 3), 10))
    results = []
    for i in range(n):
        t0 = time.perf_counter()
        try:
            s = socket.create_connection((host.strip(), int(port)), timeout=3)
            dt = (time.perf_counter() - t0) * 1000
            s.close()
            results.append(f"{dt:.1f}ms")
        except OSError:
            results.append("fail")
        if i < n - 1:
            time.sleep(0.2)
    ok = sum(1 for r in results if r != "fail")
    return f"{host}:{port} {ok}/{n} connect({' ,'.join(results)})"


def port_range_expand(spec: str = "") -> str:
    s = (spec or "").replace(" ", "")
    if not s:
        return "Error: spec required (e.g. 80,443,8000-8005)"
    ports = []
    for part in s.split(","):
        if not part:
            continue
        if "-" in part:
            try:
                lo, hi = part.split("-", 1)
                lo_i, hi_i = int(lo), int(hi)
            except ValueError:
                return f"Error: bad range {part!r}"
            if not (0 < lo_i <= hi_i <= 65535) or hi_i - lo_i > 4096:
                return f"Error: bad/oversized range {part!r}"
            ports.extend(range(lo_i, hi_i + 1))
        else:
            try:
                p = int(part)
            except ValueError:
                return f"Error: bad port {part!r}"
            if not 0 < p <= 65535:
                return f"Error: port {part!r} out of range"
            ports.append(p)
    uniq = sorted(set(ports))
    return f"{len(uniq)} ports: {','.join(map(str, uniq[:500]))}{' ...' if len(uniq) > 500 else ''}"


def is_reachable(targets: str = "") -> str:
    if not targets:
        return "Error: targets required (host:port,host:port)"
    out = []
    for t in [x.strip() for x in targets.split(",") if x.strip()][:30]:
        if ":" not in t:
            out.append(f"{t}: BAD SPEC (need host:port)")
            continue
        h, _, p = t.rpartition(":")
        try:
            s = socket.create_connection((h, int(p)), timeout=2)
            s.close()
            out.append(f"{t}: OPEN")
        except OSError:
            out.append(f"{t}: closed/filtered")
    return "\n".join(out)
