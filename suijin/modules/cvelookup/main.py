"""cvelookup — thin alias of the canonical search_cve engine.

The old divergent NVD client was deleted — one engine, one cache,
one output format (intel.py search_cve). This module keeps the pack
manifest's tool name alive so existing references don't break.
"""

from __future__ import annotations


def cve_search_nvd(keyword: str = "", api_key: str = "", limit: int = 10) -> str:
    """Alias of search_cve (intel.py) — the one CVE engine."""
    from suijin.modules.tools.lib.intel import search_cve

    return search_cve(keyword, config=None, version="", limit=limit)
