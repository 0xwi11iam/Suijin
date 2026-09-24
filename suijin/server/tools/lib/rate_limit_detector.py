"""
Rate Limit Detector — track response times and status codes per endpoint.
Detects rate limiting patterns and recommends backoff strategies.
"""

from __future__ import annotations

import time
from collections import defaultdict

_tracker = defaultdict(
    lambda: {
        "requests": 0,
        "timestamps": [],
        "statuses": [],
        "latencies_ms": [],
        "rate_limited": False,
        "backoff_until": 0,
        "consecutive_429s": 0,
    }
)


def record_request(endpoint: str, status_code: int, latency_ms: float):
    """Record a request to track rate limiting."""
    now = time.time()
    entry = _tracker[endpoint]
    entry["requests"] += 1
    entry["timestamps"].append(now)
    entry["statuses"].append(status_code)
    entry["latencies_ms"].append(latency_ms)

    # Trim old entries (keep last 5 minutes)
    cutoff = now - 300
    entry["timestamps"] = [t for t in entry["timestamps"] if t > cutoff]
    entry["statuses"] = entry["statuses"][-len(entry["timestamps"]) :]
    entry["latencies_ms"] = entry["latencies_ms"][-len(entry["timestamps"]) :]

    if status_code == 429:
        entry["consecutive_429s"] += 1
        if entry["consecutive_429s"] >= 2:
            entry["rate_limited"] = True
            backoff = min(10 * (2 ** entry["consecutive_429s"]), 120)
            entry["backoff_until"] = now + backoff
    else:
        entry["consecutive_429s"] = 0


def check_rate_limit(endpoint: str) -> dict:
    """Check if an endpoint is currently rate-limited and get recommendation."""
    entry = _tracker.get(endpoint, {})
    now = time.time()
    recent = len(entry.get("timestamps", []))
    latencies = entry.get("latencies_ms", [])
    avg_latency = sum(latencies) / max(len(latencies), 1)

    result = {
        "endpoint": endpoint,
        "requests_total": entry.get("requests", 0),
        "requests_recent_5m": recent,
        "avg_latency_ms": round(avg_latency, 1),
        "rate_limited": entry.get("rate_limited", False),
        "consecutive_429s": entry.get("consecutive_429s", 0),
    }

    if entry.get("rate_limited", False) and entry.get("backoff_until", 0) > now:
        wait = int(entry["backoff_until"] - now)
        result["recommendation"] = f"Rate limited. Wait {wait}s before next request."
    elif recent > 50:
        result["recommendation"] = "High request rate. Slow down to 1 req/s."
    elif avg_latency > 2000:
        result["recommendation"] = "High latency. Consider increasing timeout."
    else:
        result["recommendation"] = "Normal. No rate limiting detected."

    return result


def get_all_endpoints_status() -> str:
    """Get rate limit status for all tracked endpoints."""
    lines = []
    for ep in sorted(_tracker.keys()):
        info = check_rate_limit(ep)
        status = "BLOCKED" if info["rate_limited"] else "OK"
        lines.append(f"[{status}] {ep}: {info['requests_recent_5m']} req/5m, {info['avg_latency_ms']}ms avg")
    return "\n".join(lines) if lines else "(no endpoints tracked)"


def reset():
    """Reset all tracking data."""
    _tracker.clear()
