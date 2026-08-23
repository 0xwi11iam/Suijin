"""creds_leaker — MALICIOUS EXAMPLE, never install.

Scanner exercise: a hardcoded cloud credential in source. The secret
patterns must flag it critical and the install must refuse.
"""


def check_config() -> str:
    """Check the config."""
    return "ok"


AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
