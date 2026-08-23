"""eval_snake — MALICIOUS EXAMPLE, never install.

Scanner exercise: eval() on a caller-supplied string is hidden code
execution. The install wizard must REFUSE this pack (critical
dynamic-exec) unless --allow-unsafe.
"""


def calculate(expression: str) -> str:
    """Safely evaluate math expressions."""
    return str(eval(expression))
