"""Dormant example addon (the leading underscore keeps it unloaded)."""


def shout(text: str = "") -> str:
    """Uppercase a string with emphasis."""
    if not text:
        return "Error: text required"
    return text.upper() + "!"
