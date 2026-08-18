"""LLM response parsing — Pydantic-based structured output extraction.

Replaces suijin's regex-based robust_extract_tool() with type-validated
parsing that retries on malformed JSON.

Ported from redamon/agentic/orchestrator_helpers/parsing.py.
"""

from __future__ import annotations

import json
import logging

# Token patterns and vuln classification — defined inline
import re as _re
from typing import Optional

from .json_utils import extract_json, repair_trailing_json_delimiters

TOKEN_PATTERNS = {
    "jwt": _re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", _re.IGNORECASE),
    "api_key": _re.compile(r"(?:api[_-]?key|apikey)[:=]\s*['\"]?([A-Za-z0-9_-]{20,})['\"]?", _re.IGNORECASE),
    "aws_iam": _re.compile(r"AKIA[0-9A-Z]{16}"),
    "github_token": _re.compile(r"gh[pousr]_[A-Za-z0-9_]{36}"),
    "bearer": _re.compile(r"Bearer\s+([A-Za-z0-9_\-\.]+)", _re.IGNORECASE),
}
SESSION_COOKIE_NAMES = {"session", "token", "auth", "jwt", "sid", "connect.sid", "PHPSESSID", "JSESSIONID"}


def extract_tokens_from_response(body: str) -> list:
    found = []
    for token_type, pattern in TOKEN_PATTERNS.items():
        for match in pattern.finditer(body):
            found.append({"type": token_type, "value": match.group(0), "position": match.start()})
    return found


def is_exploit_successful(status_code: int) -> bool:
    return status_code in {200, 201, 202, 204, 301, 302}


OWASP_TOP_10 = {
    "A01": {"name": "Broken Access Control", "cwe": "CWE-284"},
    "A03": {"name": "Injection", "cwe": "CWE-74"},
    "A05": {"name": "Security Misconfiguration", "cwe": "CWE-16"},
    "A10": {"name": "SSRF", "cwe": "CWE-918"},
}
WEB_VULN_INDICATORS = {
    "sqli": {
        "error_strings": ["SQL syntax", "mysql_fetch", "ORA-", "PostgreSQL", "unclosed quotation"],
        "owasp_category": "A03",
    },
    "xss": {
        "reflection_check": "<script>alert(1)</script>",
        "contexts": ["html_body", "attribute_value"],
        "owasp_category": "A03",
    },
}


def classify_web_vuln(indicator_text: str) -> list:
    matches = []
    lowered = indicator_text.lower()
    for vuln_type, info in WEB_VULN_INDICATORS.items():
        for err in info.get("error_strings", []):
            if err.lower() in lowered:
                matches.append({"type": vuln_type, "owasp": info.get("owasp_category"), "confidence": "medium"})
                break
    return matches


logger = logging.getLogger(__name__)


def try_parse_llm_decision(response_text: str) -> tuple[Optional[dict], Optional[str]]:
    """Attempt to parse an LLM decision from JSON response.

    Returns (decision_dict, None) on success, or (None, error_message) on failure.
    The decision_dict contains: action, thought, reasoning, tool_name, tool_args,
    output_analysis, todo_updates, phase_transition, etc.

    This is intentionally flexible — we validate structurally but don't enforce
    a strict Pydantic schema at this layer. The think_node applies semantic
    validation based on the action type.
    """
    try:
        json_str = extract_json(response_text)
        if not json_str:
            return None, "No JSON object found in response"

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as original_error:
            repaired_json = repair_trailing_json_delimiters(json_str)
            if not repaired_json:
                raise
            try:
                data = json.loads(repaired_json)
            except json.JSONDecodeError as repair_error:
                raise original_error from repair_error
            logger.warning(
                "Recovered LLM JSON by appending %d trailing delimiter(s)",
                len(repaired_json) - len(json_str),
            )

        # Basic structural validation
        if not isinstance(data, dict):
            return None, "Parsed JSON is not an object"

        action = data.get("action")
        if not action:
            return None, "Missing required field: 'action'"

        valid_actions = {
            "use_tool",
            "plan_tools",
            "transition_phase",
            "complete",
            "ask_user",
            "deploy_subagent",
            "switch_skill",
        }
        if action not in valid_actions:
            return None, f"Unknown action: '{action}'. Must be one of {valid_actions}"

        # If use_tool or plan_tools, require tool_name
        if action == "use_tool" and not data.get("tool_name"):
            return None, "action=use_tool requires 'tool_name'"

        return data, None

    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"
    except Exception as e:
        return None, f"Parse error: {e}"
