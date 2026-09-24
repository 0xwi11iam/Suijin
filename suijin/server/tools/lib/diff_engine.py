"""
Diff Engine — compare HTTP responses before/after parameter injection.
Auto-detects anomalies: status changes, length differences, new content.
"""

from __future__ import annotations

import re


def diff_responses(baseline: str, injected: str, sensitivity: str = "medium") -> dict:
    """Compare two HTTP response strings and detect anomalies.

    Args:
        baseline: The normal (uninjected) response.
        injected: The response after parameter injection.
        sensitivity: 'low', 'medium', or 'high' for anomaly threshold.

    Returns:
        Dict with analysis results.
    """
    if not baseline or not injected:
        return {"error": "Both baseline and injected responses required"}

    thresholds = {"low": 0.15, "medium": 0.05, "high": 0.01}
    threshold = thresholds.get(sensitivity, 0.05)

    result = {
        "baseline_length": len(baseline),
        "injected_length": len(injected),
        "length_diff": len(injected) - len(baseline),
        "length_diff_pct": round((len(injected) - len(baseline)) / max(len(baseline), 1) * 100, 2),
        "anomalies": [],
    }

    # Check status code changes
    base_status = _extract_status(baseline)
    inj_status = _extract_status(injected)
    if base_status and inj_status and base_status != inj_status:
        result["anomalies"].append(
            {
                "type": "status_change",
                "baseline": base_status,
                "injected": inj_status,
                "detail": f"Status changed from {base_status} to {inj_status}",
            }
        )

    # Check for error patterns
    error_patterns = [
        (r"(?i)(sql|syntax|mysql|sqlite|postgresql|oracle|odbc|mysql_fetch)", "SQL Error"),
        (r"(?i)(stack trace|traceback|exception|error at)", "Stack Trace"),
        (r"(?i)(warning|notice|deprecated)", "Warning/Notice"),
        (r"(?i)(admin|root|password|secret|token|key)", "Sensitive Data Leak"),
    ]
    for pattern, label in error_patterns:
        in_base = bool(re.search(pattern, baseline))
        in_inj = bool(re.search(pattern, injected))
        if not in_base and in_inj:
            result["anomalies"].append(
                {
                    "type": "error_disclosure",
                    "label": label,
                    "detail": f"{label} appeared in injected response but not baseline",
                }
            )

    # Significant length change
    if abs(result["length_diff_pct"]) > (threshold * 100):
        direction = "larger" if result["length_diff"] > 0 else "smaller"
        result["anomalies"].append(
            {
                "type": "length_anomaly",
                "direction": direction,
                "detail": f"Response is {abs(result['length_diff_pct']):.1f}% {direction} than baseline",
            }
        )

    # Boolean comparison
    result["is_different"] = len(baseline) != len(injected) or baseline.strip() != injected.strip()
    result["anomaly_count"] = len(result["anomalies"])

    return result


def _extract_status(response: str) -> str:
    """Extract HTTP status code from response string."""
    m = re.search(r"(?:^|\n)(?:HTTP/[\d.]+ )?(\d{3})", response)
    return m.group(1) if m else ""


def quick_diff(baseline_body: str, injected_body: str) -> str:
    """Quick text diff for two response bodies. Returns human-readable summary."""
    bl = baseline_body.strip()
    il = injected_body.strip()
    if bl == il:
        return "No differences detected."
    lines = [
        f"Baseline: {len(bl)} chars",
        f"Injected: {len(il)} chars",
        f"Delta: {len(il) - len(bl)} chars ({(len(il) - len(bl)) / max(len(bl), 1) * 100:.1f}%)",
    ]
    # Find first differing line
    bl_lines = bl.split("\n")
    il_lines = il.split("\n")
    for i in range(min(len(bl_lines), len(il_lines))):
        if bl_lines[i] != il_lines[i]:
            lines.append(f"First diff at line {i + 1}:")
            lines.append(f"  Base: {bl_lines[i][:100]}")
            lines.append(f"  Inj:  {il_lines[i][:100]}")
            break
    return "\n".join(lines)
