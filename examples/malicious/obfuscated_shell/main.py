"""obfuscated_shell — MALICIOUS EXAMPLE, never install.

Scanner exercise: a large encoded blob whose decoded form feeds exec().
Both the obfuscation and dynamic-exec rules must fire.
"""
import base64

_BLOB = "A" * 250


def run_helper() -> str:
    """Helper."""
    exec(base64.b64decode(_BLOB))
    return "done"


exec(_BLOB)
