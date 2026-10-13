"""Analysis & reporting tool wrappers (payloads, diffing, rate limits, reports)."""

from __future__ import annotations


def _payload_gen(vuln_type: str, framework: str = "") -> str:
    from suijin.modules.tools.lib.payload_generator import generate_payloads, list_payload_types

    if not vuln_type:
        return list_payload_types()
    return generate_payloads(vuln_type, framework=framework)


def _diff_resp(baseline: str, injected: str, sensitivity: str = "medium") -> str:
    from suijin.modules.tools.lib.diff_engine import diff_responses, quick_diff

    if len(baseline) < 200 and "http" not in baseline.lower():
        return quick_diff(baseline, injected)
    import json

    return json.dumps(diff_responses(baseline, injected, sensitivity), indent=2)


def _rate_check(endpoint: str) -> str:
    import json

    from suijin.modules.tools.lib.rate_limit_detector import check_rate_limit

    return json.dumps(check_rate_limit(endpoint), indent=2)


def _rate_all() -> str:
    from suijin.modules.tools.lib.rate_limit_detector import get_all_endpoints_status

    return get_all_endpoints_status()


def _attack_tree(trace_json: str) -> str:
    import json

    from suijin.modules.tools.lib.attack_tree import build_attack_tree

    trace = json.loads(trace_json) if trace_json else []
    return build_attack_tree(trace)


def _gen_report(engagement: str, trace_json: str = "", findings_json: str = "") -> str:
    """Generate the engagement report FROM THE AUDIT TRAIL — the on-disk
    truth (findings with real keys, thoughts, cost, completion). The
    model-pasted JSON args are a LAST-RESORT fallback for pre-trail
    calls only; the old behavior (model hand-serializing the trace)
    produced truncated JSON, empty finding rows and $0.00 costs."""
    import json

    from suijin.modules.tools.lib.audit_trail import current_trail
    from suijin.modules.tools.lib.report_exporter import generate_report

    trail = current_trail(engagement or None)
    if trail:
        from suijin.modules.tools.lib.audit_trail import report_inputs

        trace, findings, cost, completion = report_inputs(trail)
    else:
        trace = json.loads(trace_json) if trace_json else []
        findings = json.loads(findings_json) if findings_json else []
        cost, completion = 0.0, ""
    return generate_report(engagement, trace, findings, {}, [], cost, completion)
