"""Tool contracts — schemas + usage docs for the agent's tools.

Each contract is a JSON schema (input validation with CLEAR errors) +
a usage doc that rides the tool catalog so the model knows HOW to call,
not just THAT a tool exists. The CyberStrike lesson: tools that teach
their own usage get used correctly the first time.

Loaded by dispatch.py at route time: invalid args are rejected with the
schema's error message (never a deep AttributeError), and the usage docs
are injected into the tool catalog section of the think context.
"""

from __future__ import annotations

import json
from pathlib import Path

_CONTRACTS_DIR = Path(__file__).resolve().parent

_cache: dict[str, dict] | None = None


def _load() -> dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache
    out: dict[str, dict] = {}
    for d in sorted(_CONTRACTS_DIR.iterdir()):
        schema_p = d / "schema.json"
        usage_p = d / "usage.md"
        if not schema_p.is_file():
            continue
        try:
            schema = json.loads(schema_p.read_text())
        except (OSError, ValueError):
            continue
        usage = usage_p.read_text(encoding="utf-8") if usage_p.is_file() else ""
        out[d.name] = {"schema": schema, "usage": usage}
    _cache = out
    return out


def contracts() -> dict[str, dict]:
    """All loaded contracts: tool_name -> {schema, usage}."""
    return _load()


def validate(tool_name: str, args: dict) -> str | None:
    """Validate tool args against the contract schema. Returns an error
    message string when invalid, None when OK (or no contract exists —
    uncontracted tools pass through unchanged, gradual adoption).
    """
    c = _load().get(tool_name)
    if not c:
        return None
    schema = c["schema"]
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    for k in required:
        if k not in args or args[k] in (None, "", {}):
            return (
                f"Missing required argument '{k}'. "
                f"Required: {', '.join(sorted(required))}. "
                f"See the tool usage guide."
            )

    for k, v in args.items():
        if k.startswith("_"):
            continue  # system flags pass through
        spec = props.get(k)
        if spec is None:
            if schema.get("additionalProperties") is False:
                allowed = ", ".join(sorted(props))
                return f"Unknown argument '{k}'. Accepted: {allowed}."
            continue
        t = str(spec.get("type") or "")
        if t == "string" and not isinstance(v, str):
            return f"Argument '{k}' must be a string (got {type(v).__name__}). {spec.get('description', '')}"
        if t == "integer" and not isinstance(v, int):
            return f"Argument '{k}' must be an integer (got {type(v).__name__}). {spec.get('description', '')}"
        if t == "boolean" and not isinstance(v, bool):
            return f"Argument '{k}' must be true or false (got {type(v).__name__}). {spec.get('description', '')}"
        if t == "array" and not isinstance(v, list):
            return f"Argument '{k}' must be an array (got {type(v).__name__}). {spec.get('description', '')}"
        if t == "object" and not isinstance(v, dict):
            return (
                f"Argument '{k}' must be an object/dict (got {type(v).__name__}). "
                f"{spec.get('description', 'Pass a JSON object, not a bare value.')}"
            )
        enum = spec.get("enum")
        if enum and v not in enum:
            return f"Argument '{k}' must be one of: {', '.join(map(str, enum))} (got {v!r})."
    return None


def usage_docs(limit: int = 12) -> str:
    """The usage docs for the context — most-called tools first,
    capped so the context doesn't bloat."""
    lines: list[str] = []
    for name, c in list(_load().items())[:limit]:
        if c["usage"]:
            lines.append(f"### {name}\n{c['usage']}")
    return "\n\n".join(lines) if lines else ""


def reset_cache() -> None:
    """Tests reload contracts fresh."""
    global _cache
    _cache = None
