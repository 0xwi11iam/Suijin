"""Analysis & reporting tool wrappers (payloads, diffing, rate limits, reports)."""
from __future__ import annotations


def _payload_gen(vuln_type: str, framework: str = "") -> str:
    from medusa.tools.payload_generator import generate_payloads, list_payload_types
    if not vuln_type:
        return list_payload_types()
    return generate_payloads(vuln_type, framework=framework)


def _diff_resp(baseline: str, injected: str, sensitivity: str = "medium") -> str:
    from medusa.tools.diff_engine import diff_responses, quick_diff
    if len(baseline) < 200 and "http" not in baseline.lower():
        return quick_diff(baseline, injected)
    import json
    return json.dumps(diff_responses(baseline, injected, sensitivity), indent=2)


def _rate_check(endpoint: str) -> str:
    import json

    from medusa.tools.rate_limit_detector import check_rate_limit
    return json.dumps(check_rate_limit(endpoint), indent=2)


def _rate_all() -> str:
    from medusa.tools.rate_limit_detector import get_all_endpoints_status
    return get_all_endpoints_status()


def _attack_tree(trace_json: str) -> str:
    import json

    from medusa.tools.attack_tree import build_attack_tree
    trace = json.loads(trace_json) if trace_json else []
    return build_attack_tree(trace)


def _gen_report(engagement: str, trace_json: str, findings_json: str) -> str:
    import json

    from medusa.tools.report_exporter import generate_report
    trace = json.loads(trace_json) if trace_json else []
    findings = json.loads(findings_json) if findings_json else []
    return generate_report(engagement, trace, findings, {}, [], 0)
