"""Race/concurrency harness — TOCTOU testing for the critical finder.

Fires N concurrent requests and diffs responses. Detects:
- state corruption (same request, different results when concurrent)
- rate-limit bypass (N requests succeed where sequential would throttle)
- inventory/reservation races (oversell, double-spend)
- timing side channels (response time variance under load)

THE CONTRACT:
  race_test(url, method, payload, count, delay_ms, headers) -> str
    — fires `count` copies of the same request concurrently
    — reports: status distribution, response diffs, timing variance
"""

from __future__ import annotations

import concurrent.futures
import time
from collections import Counter


def _fire_one(url: str, method: str, payload: str, headers: dict) -> dict:
    """Fire a single HTTP request using urllib (no external deps)."""
    import urllib.error
    import urllib.request

    started = time.monotonic()
    status = 0
    body = ""
    try:
        req = urllib.request.Request(url, method=method.upper())
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        if payload and method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            req.data = payload.encode("utf-8")
            req.add_header(
                "Content-Type", "application/json" if payload.startswith("{") else "application/x-www-form-urlencoded"
            )
        resp = urllib.request.urlopen(req, timeout=15)
        status = resp.status
        body = resp.read().decode("utf-8", errors="replace")[:500]
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode("utf-8", errors="replace")[:500]
    except Exception as e:
        body = f"error: {e}"
    elapsed_ms = (time.monotonic() - started) * 1000
    return {"status": status, "body": body, "ms": round(elapsed_ms, 1)}


def race_test(
    url: str,
    method: str = "POST",
    payload: str = "",
    count: int = 5,
    delay_ms: int = 0,
    headers: dict | None = None,
) -> str:
    """Fire N concurrent requests and analyze the results for race conditions."""
    count = max(2, min(int(count or 5), 20))
    headers = headers or {}

    if delay_ms > 0:
        time.sleep(delay_ms / 1000)

    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as executor:
        futures = [executor.submit(_fire_one, url, method, payload, headers) for _ in range(count)]
        results = [f.result() for f in futures]

    # analysis
    statuses = Counter(r["status"] for r in results)
    bodies = [r["body"] for r in results]
    unique_bodies = len(set(bodies))
    timings = [r["ms"] for r in results]
    avg_ms = sum(timings) / len(timings)

    lines = [
        f"RACE TEST: {method} {url[:100]} x{count} concurrent",
        f"Status distribution: {dict(statuses)}",
        f"Unique response bodies: {unique_bodies} of {count}",
        f"Timing: avg {avg_ms:.0f}ms, min {min(timings):.0f}ms, max {max(timings):.0f}ms",
    ]

    # detect anomalies
    anomalies = []
    if unique_bodies > 1:
        anomalies.append(
            f"RESPONSE DIVERGENCE: {unique_bodies} different bodies from identical concurrent requests — potential state race"
        )
    if len(statuses) > 1:
        anomalies.append(f"STATUS SPLIT: mixed statuses {dict(statuses)} — some requests saw different state")
    if max(timings) > 0 and min(timings) > 0 and max(timings) / max(0.1, min(timings)) > 10:
        anomalies.append(
            f"TIMING ANOMALY: max/min ratio {max(timings) / max(0.1, min(timings)):.0f}x — possible lock contention or queue overflow"
        )

    if anomalies:
        lines.append("\nANOMALIES DETECTED:")
        lines.extend(f"  ⚠ {a}" for a in anomalies)
        lines.append("\nThis MAY be a race condition — verify by:")
        lines.append("  1. Run the same request sequentially — does it always give the same answer?")
        lines.append("  2. Check if the divergence has security impact (oversell, double-spend, auth bypass)")
        lines.append("  3. If confirmed, catalog with a POC that fires concurrent requests")
    else:
        lines.append("\nNo race anomalies detected (uniform responses).")

    # show first 3 bodies for inspection
    lines.append("\nSample responses:")
    for i, r in enumerate(results[:3]):
        lines.append(f"  [{i}] {r['status']} ({r['ms']:.0f}ms): {r['body'][:100]}")

    return "\n".join(lines)


def cred_matrix(
    objects: list,
    credentials: list,
    method: str = "GET",
    base_url: str = "",
) -> str:
    """Cross-credential access testing: every credential against every object.
    The systematic IDOR finder.

    objects: list of URLs or object IDs
    credentials: list of auth headers (e.g. [{"Authorization": "Bearer <tok1>"}, ...])
    """
    if not objects or not credentials:
        return "Usage: cred_matrix(objects=[url1, url2], credentials=[{auth1}, {auth2}])"

    lines = [
        f"CRED MATRIX: {len(credentials)} credentials × {len(objects)} objects = {len(credentials) * len(objects)} tests\n"
    ]

    access_matrix = {}
    anomalies = []

    for ci, cred in enumerate(credentials):
        for oi, obj in enumerate(objects):
            url = obj if obj.startswith("http") else f"{base_url}/{obj}" if base_url else obj
            result = _fire_one(url, method, "", cred if isinstance(cred, dict) else {})
            key = f"cred{ci}_obj{oi}"
            access_matrix[key] = result
            status = result["status"]
            # flag cross-credential access: cred_i can read obj_j where j belongs to cred_j
            if status == 200 and ci != oi:
                anomalies.append(
                    f"IDOR SUSPECT: credential {ci} can access object {oi} "
                    f"(status {status}, body: {result['body'][:80]})"
                )

    # render matrix
    lines.append("Access matrix (rows=credentials, cols=objects):")
    header = "      " + "".join(f"obj{j:>3} " for j in range(len(objects)))
    lines.append(header)
    for ci in range(len(credentials)):
        row = f"cred{ci:>2} "
        for oi in range(len(objects)):
            r = access_matrix.get(f"cred{ci}_obj{oi}", {})
            s = r.get("status", "?")
            color = "✓" if s == 200 else "✗" if s in (403, 401) else "?"
            row += f"  {color}{s:>3} "
        lines.append(row)

    if anomalies:
        lines.append("\n⚠ CROSS-CREDENTIAL ACCESS DETECTED:")
        lines.extend(f"  {a}" for a in anomalies)
        lines.append(
            "\nVerify: does credential A ACTUALLY see credential B's data (not just a 200 on a shared resource)?"
        )
        lines.append("If confirmed → catalog_exploit with class=idor")
    else:
        lines.append("\nNo cross-credential access anomalies (each credential only sees its own objects).")

    return "\n".join(lines)
