"""Guardrail-grade file permissions for agent tool calls (2026-09-12).

config.json / run config:
    "perms": {"deny_files": [".env", "*.pem", "config/secrets/**"]}

`check_perms` runs inside `dispatch.route_tool` BEFORE any tool executes:
a denied pattern matched against a tool argument — a path, a command
string, a filename — refuses the call with a sentence that names the
pattern and the tool. Config absent or empty = no checks (every existing
engagement behaves exactly as before).

THIS IS A GUARDRAIL, NOT A SANDBOX, and the distinction is the design:
the check sees tool ARGUMENTS, not what a shell does with them. A
determined agent can still read a denied file through indirection
(`python -c "open('.env')"`, base64 echoes, symlinks). What it does
reliably prevent is the ORDINARY path — the model casually `cat`-ing
secrets while exploring — which is the measured behavior class. Honest
labelling everywhere: the run config template and the docs both say
guardrail-grade. True isolation is a container project, not a dispatch
hook.
"""

from __future__ import annotations

import fnmatch
import os


def _denied_patterns(config: dict | None) -> list[str]:
    perms = (config or {}).get("perms") or {}
    pats = perms.get("deny_files") or []
    return [str(p) for p in pats if str(p).strip()]


def _matches(value: str, pattern: str) -> bool:
    """A pattern denies a value when it globs the whole value, globs the
    basename (directory prefixes shouldn't rescue a denied file), or
    appears verbatim inside it (the `cat config/.env` case — the pattern
    is a substring of the argument, not the whole thing)."""
    v = value.strip()
    if not v:
        return False
    if fnmatch.fnmatch(v, pattern) or fnmatch.fnmatch(os.path.basename(v), pattern):
        return True
    return pattern in v


def check_perms(tool_name: str, args, config: dict | None) -> str | None:
    """None = allowed. A refusal sentence = denied. Never raises — a perms
    check that crashes the dispatch would be a worse outcome than no
    check (invariant: the guard may not break the run it guards)."""
    try:
        patterns = _denied_patterns(config)
        if not patterns or not isinstance(args, dict):
            return None
        for key, value in args.items():
            if not isinstance(value, str) or not value.strip():
                continue
            # long payloads (code bodies, notes) only get the substring
            # test — globbing a 2,000-char string against every pattern
            # is waste; the verbatim leak is what we are catching
            for pattern in patterns:
                hit = pattern in value if len(value) > 400 else _matches(value, pattern)
                if hit:
                    return (
                        f"Error: permission denied — pattern {pattern!r} (perms.deny_files) "
                        f"matched argument {key!r} of {tool_name!r}. This is a guardrail, "
                        f"not a sandbox; ask the operator if the access is legitimate."
                    )
        return None
    except Exception:  # noqa: BLE001 — the guard may not break the run
        return None
