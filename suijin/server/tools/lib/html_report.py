"""HTML report exporter — styled, collapsible, client-deliverable reports."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def _workspace_dir():
    """Workspace dir (honours a monkeypatched module attr)."""
    v = globals().get("WORKSPACE_DIR")
    if v is not None:
        return v
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR as _W

    return _W


def __getattr__(name):
    if name == "WORKSPACE_DIR":
        return _workspace_dir()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def export_html(
    findings: list, engagement_name: str, cost_usd: float = 0.0, trace_count: int = 0, output_path: str = None
) -> str:
    path = output_path or str(
        _workspace_dir()
        / "reports"
        / f"{engagement_name.replace(' ', '_')}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.html"
    )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    rows = ""
    for i, f in enumerate(findings[:50], 1):
        sev = f.get("severity", "info").upper()
        color = {
            "CRITICAL": "#7b0000",
            "HIGH": "#cc0000",
            "MEDIUM": "#cc6600",
            "LOW": "#336600",
            "INFO": "#336699",
        }.get(sev, "#333")
        rows += f"<tr><td>{i}</td><td style='color:{color}'><b>{sev}</b></td><td>{f.get('type', '?')}</td><td>{f.get('endpoint', '?')}</td><td>{f.get('description', '')[:200]}</td></tr>"
    html = f"""<!DOCTYPE html><html><head><title>Suijin Report — {engagement_name}</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:900px;margin:0 auto;padding:20px;background:#0d1117;color:#c9d1d9}}
h1{{color:#58a6ff}}h2{{color:#f0883e;border-bottom:1px solid #30363d}}table{{width:100%;border-collapse:collapse}}
th{{background:#161b22;text-align:left;padding:8px}}td{{padding:8px;border-top:1px solid #30363d}}
.section{{margin:30px 0}}pre{{background:#161b22;padding:15px;border-radius:6px;overflow-x:auto}}
</style></head><body><h1>Suijin Engagement Report</h1>
<p><b>Engagement:</b> {engagement_name} | <b>Generated:</b> {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")} | <b>Cost:</b> ${cost_usd:.4f} | <b>Steps:</b> {trace_count}</p>
<div class='section'><h2>Findings ({len(findings)})</h2><table><tr><th>#</th><th>Severity</th><th>Type</th><th>Endpoint</th><th>Description</th></tr>{rows}</table></div>
<div class='section'><h2>Remediation Summary</h2><p>Prioritize critical and high severity findings. Validate all fixes with retesting.</p></div>
</body></html>"""
    Path(path).write_text(html)
    return path
