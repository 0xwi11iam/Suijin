"""mitmproxy addon — full HTTPS ingestion (the deep path).

Usage:  mitmdump -s suijin/server/tools/lib/ingest_addon.py

Requires mitmproxy installed + its CA trusted in your browser. Every
decrypted request/response flows into the traffic index — headers,
cookies, bodies — building the agent's asset graph from YOUR browsing.
"""

from suijin.server.tools.lib.ingest import record_traffic


def response(flow):
    """Every response: record it."""
    try:
        record_traffic(
            method=flow.request.method,
            url=flow.request.pretty_url,
            status=flow.response.status_code,
            req_headers=dict(flow.request.headers),
            resp_headers=dict(flow.response.headers),
            body_preview=flow.response.text[:500] if flow.response.content else "",
        )
    except Exception:
        pass  # the addon never breaks the proxy
