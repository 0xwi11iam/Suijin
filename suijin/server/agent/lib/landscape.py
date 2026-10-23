"""Target landscape — the breadth dimension the agent was missing.

The operator's insight (2026-10-18, a live engagement on a large
platform): "it's like looking for a criminal and searching one street."
The agent had NO concept of product families. It saw endpoints, not
domains — most of one run's iterations went to a single redirector
because nothing told the agent the other 99% of the target existed.

This module:
1. BOOT: detect product families from the target hostname (a lookup +
   quick probe pass)
2. RENDER: a landscape section in the think context showing explored
   vs untested families with a breadth percentage
3. BREADTH GATE: when >60% of recent iterations target one family AND
   untested families exist, inject a rotation directive
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Family:
    name: str
    domains: list[str]
    explored: bool = False
    iterations: int = 0


#: platform → product-family maps. SHIPPED EMPTY (operator ruling
#: 2026-10-23: nothing engagement-derived in the tree — the maps that
#: used to live here were built during live engagements). Operators
#: carry their own maps in config.json `landscape_families`:
#:   {"landscape_families": {"<base-domain>": [{"name": ..., "domains": [...]}]}}
#: dict form is per-platform (matched by base domain); list form applies
#: to any target (single-platform operators).
_KNOWN_FAMILIES: dict[str, list[dict]] = {}

#: fallback for unknown targets — generic web app families
_GENERIC_FAMILIES = [
    {"name": "Web Application", "domains": []},
    {"name": "API Surface", "domains": []},
    {"name": "Authentication", "domains": []},
    {"name": "File & Upload", "domains": []},
]


def detect_families(target: str, config: dict | None = None) -> list[Family]:
    """Detect product families for a target hostname."""
    target = str(target or "").lower().strip()
    # strip scheme and path
    target = re.sub(r"^https?://", "", target).split("/")[0].split(":")[0]
    # strip www.
    if target.startswith("www."):
        target = target[4:]

    # config override — dict form is per-platform (base-domain keyed),
    # list form applies to any target
    custom = (config or {}).get("landscape_families")
    if isinstance(custom, dict):
        for base, fams in custom.items():
            if target == base or target.endswith(f".{base}"):
                return [Family(name=f["name"], domains=f.get("domains", [])) for f in fams]
    elif isinstance(custom, list) and custom:
        return [Family(name=f["name"], domains=f.get("domains", [])) for f in custom]

    # known base domain match (the target's own subdomains count)
    for base, fams in _KNOWN_FAMILIES.items():
        if target == base or target.endswith(f".{base}"):
            return [Family(name=f["name"], domains=f["domains"]) for f in fams]

    # generic fallback
    return [Family(name=f["name"], domains=f["domains"]) for f in _GENERIC_FAMILIES]


def classify_url(url: str, families: list[Family]) -> str | None:
    """Which family does this URL belong to? None if unclassified."""
    url = str(url or "").lower()
    for fam in families:
        for domain in fam.domains:
            if domain in url:
                return fam.name
            # wildcard match (*.googleapis.com)
            if domain.startswith("*."):
                suffix = domain[1:]  # ".googleapis.com"
                # check if any subdomain of this suffix is in the URL
                if suffix in url:
                    return fam.name
    return None


def update_exploration(families: list[Family], recent_urls: list[str], recent_iterations: int) -> list[Family]:
    """Update family exploration status based on recent tool URLs."""
    for url in recent_urls:
        fam_name = classify_url(url, families)
        if fam_name:
            for f in families:
                if f.name == fam_name:
                    f.explored = True
                    f.iterations += 1
    return families


def render_landscape(families: list[Family]) -> str:
    """The landscape section for the think context."""
    if not families:
        return ""
    total = len(families)
    explored = sum(1 for f in families if f.explored)
    pct = round(100 * explored / max(1, total))
    untested = [f.name for f in families if not f.explored]

    lines = [
        f"## TARGET LANDSCAPE — {explored}/{total} product families explored ({pct}%)",
        "",
    ]
    for f in families:
        mark = "✓" if f.explored else "✗"
        iter_count = f" ({f.iterations} iterations)" if f.iterations else ""
        lines.append(f"  {mark} {f.name}{iter_count}")

    if untested:
        lines.append(f"\n  ⚠ {len(untested)} families UNTESTED: {', '.join(untested[:5])}")
        if len(untested) > 5:
            lines.append(f"    (+{len(untested) - 5} more)")
        lines.append("  Rotate: test an untested family before going deeper on explored ones.")

    return "\n".join(lines)


def breadth_alert(
    families: list[Family], recent_urls: list[str], window: int = 20, threshold: float = 0.6
) -> str | None:
    """Check if the agent is tunnel-visioning on one family. Returns a
    rotation directive or None."""
    if not recent_urls or not families:
        return None

    # classify the recent URLs
    recent_families: list[str] = []
    for url in recent_urls[-window:]:
        fam = classify_url(url, families)
        recent_families.append(fam or "unclassified")

    # find the dominant family
    from collections import Counter

    counts = Counter(recent_families)
    dominant, count = counts.most_common(1)[0]
    if dominant == "unclassified":
        return None  # can't classify — no alert

    ratio = count / len(recent_families)
    untested = [f.name for f in families if not f.explored]

    if ratio >= threshold and untested:
        return (
            f"BREADTH ALERT — {int(ratio * 100)}% of your last {len(recent_families)} actions target "
            f"[{dominant}]. {len(untested)} product families remain UNTESTED. "
            f"Rotate now: pick one untested family ({', '.join(untested[:3])}) and enumerate its surface. "
            f"Your current chain will still be here when you return."
        )
    return None
