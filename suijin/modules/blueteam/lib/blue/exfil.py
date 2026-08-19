"""Dead-drop exfiltration detection (G50).

Two signals over traffic/logs:
  - DNS tunneling: absurd query lengths + high-entropy subdomains on a
    few parent domains (data encoded in labels)
  - Beaconing: near-constant inter-arrival deltas from one source
    (C2 check-in periodicity)
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    freq = {c: s.count(c) for c in set(s)}
    n = len(s)
    return -sum((v / n) * math.log2(v / n) for v in freq.values())


def detect_dns_tunneling(dns_queries: list, min_len: int = 40, min_entropy: float = 3.2) -> list:
    """Flag tunneling-style queries (long, high-entropy labels)."""
    flags = []
    for q in dns_queries or []:
        name = str(q.get("name", q) if isinstance(q, dict) else q)
        labels = name.strip(".").split(".")
        if len(labels) < 2:
            continue
        payload_label = max(labels[:-1], key=len)  # exclude TLD
        if len(payload_label) >= min_len and _entropy(payload_label.lower()) >= min_entropy:
            flags.append(
                f"TUNNEL-LIKE: {name[:70]} (label {len(payload_label)}B, entropy {_entropy(payload_label.lower()):.1f})"
            )
    return flags[:15]


def detect_beaconing(events: list, jitter_ratio: float = 0.25, min_events: int = 6) -> list:
    """Flag sources with near-periodic inter-arrival times."""
    by_src = defaultdict(list)
    for e in events or []:
        ts = e.get("ts") or e.get("timestamp")
        if ts is None:
            continue
        by_src[e.get("ip", "?")].append(float(ts))
    flags = []
    for src, times in by_src.items():
        if len(times) < min_events:
            continue
        times.sort()
        deltas = [b - a for a, b in zip(times, times[1:], strict=False) if b > a]
        if len(deltas) < min_events - 1:
            continue
        mean = statistics.mean(deltas)
        if mean <= 0:
            continue
        stdev = statistics.stdev(deltas) if len(deltas) > 1 else 0.0
        if stdev / mean <= jitter_ratio:
            flags.append(f"BEACON: {src} every ~{mean:.1f}s +/- {stdev:.1f}s over {len(times)} events")
    return flags[:10]
