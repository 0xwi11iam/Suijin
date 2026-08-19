"""Wordlist hub pack (F45) — thin wrappers over the knowledge lib."""

from suijin.modules.knowledge.lib import wordlist_hub


def wordlist_catalog() -> str:
    """List curated wordlists and their local fetch state."""
    return wordlist_hub.catalog()


def wordlist_fetch(name: str = "") -> str:
    """Fetch a curated wordlist (sha256-verified) into wordlists/."""
    return wordlist_hub.fetch(name)
