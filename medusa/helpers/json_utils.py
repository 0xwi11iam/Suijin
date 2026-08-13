"""JSON utilities for the agent orchestrator.

Ported from redamon/agentic/orchestrator_helpers/json_utils.py.
"""
from __future__ import annotations
import json
from datetime import datetime
from typing import Optional


class DateTimeEncoder(json.JSONEncoder):
    """JSON encoder that handles datetime objects."""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def json_dumps_safe(obj, **kwargs) -> str:
    """JSON dumps with datetime support."""
    return json.dumps(obj, cls=DateTimeEncoder, **kwargs)


def normalize_content(content) -> str:
    """Extract text from LLM response content.

    Handles both plain strings and list-of-content-blocks formats
    (e.g. [{"type": "text", "text": "..."}]).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def extract_json(response_text: str) -> Optional[str]:
    """Extract JSON from LLM response (may be wrapped in markdown)."""
    json_start = response_text.find("{")
    json_end = response_text.rfind("}") + 1

    if json_start >= 0 and json_end > json_start:
        return response_text[json_start:json_end]
    if json_start >= 0:
        return response_text[json_start:].removesuffix("```").rstrip()
    return None


def repair_trailing_json_delimiters(json_text: str) -> Optional[str]:
    """Close only unambiguous trailing JSON objects/arrays.

    Local models occasionally emit a complete decision but omit one or
    more final }/] characters. This scanner only closes unambiguous
    open delimiters — never inserts commas or changes values.
    """
    stack: list[str] = []
    in_string = False
    escaped = False
    matching_open = {"}": "{", "]": "["}
    closing = {"{": "}", "[": "]"}

    for char in json_text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in closing:
            stack.append(char)
        elif char in matching_open:
            if not stack or stack[-1] != matching_open[char]:
                return None
            stack.pop()

    if in_string or not stack:
        return None

    return json_text + "".join(closing[opener] for opener in reversed(stack))
