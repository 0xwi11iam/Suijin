"""ask — the ONE module that owns operator questions.

Before (2026-10-30, live-proved): question extraction lived in
think_node AND execute_tool_node; the auth-picker text in execute; the
choice parsing in redteamer. Three sites, three opinions, one loop.

Now: extraction, auth-detection, picker construction, and answer parsing
live HERE. The side-effectful pause/injection stays in redteamer (it
owns the stream lifetime) and calls this module for every decision it
makes about a question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from suijin.server.agent.lib.decision import normalize_decision

# ── extraction ────────────────────────────────────────────────────────


def question_of(raw: dict | None) -> str:
    """The question from ANY shape the model emits — normalize_decision
    is the single tolerant parse; this is the one-liner consumers call."""
    return str(normalize_decision(raw).get("question") or "")


# ── auth detection + picker ───────────────────────────────────────────

_AUTH_KEYWORDS = (
    "credential",
    "username",
    "password",
    "login",
    "authenticated access",
    "authentication",
    "sign in",
    "auth wall",
    "session cookie",
    "bearer token",
    "api key",
)

_URL_IN_TEXT = re.compile(r"https?://[^\s,)+\"]+|(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?|[\w.-]+\.\w{2,}(?::\d+)?")


def looks_like_auth_question(question: str) -> bool:
    """Free-text asks about login/credentials route to the numbered
    picker instead of a prompt the operator has to decode."""
    q = str(question or "").lower()
    return any(kw in q for kw in _AUTH_KEYWORDS)


def derive_target(text: str) -> str:
    """First host/URL in the text — the picker's target line."""
    m = _URL_IN_TEXT.search(str(text or ""))
    return m.group(0) if m else "target"


def auth_picker_question(question: str) -> str:
    """The numbered auth menu, target derived from the question text."""
    target = derive_target(question)
    return (
        f"AUTH NEEDED: {target} — authentication required\n\n"
        f"[1] Provide credentials (type: 1 user:password)\n"
        f"[2] Paste a token/cookie/API key (type: 2 <value>)\n"
        f"[3] Browser login (a Chromium window opens, you log in)\n"
        f"[4] Skip (test unauthenticated only)\n\n"
        f"Type a number (1-4) in the input box. Anything else = skip."
    )


# ── answer parsing ────────────────────────────────────────────────────


@dataclass
class AuthChoice:
    """The parsed operator answer to an auth picker ask."""

    kind: str  # "credentials" | "token" | "browser" | "skip"
    value: str = ""  # user:pass, or the token, or ""


def parse_auth_choice(answer: str) -> AuthChoice:
    """Pure parse of the operator's picker answer.

    '1 user:pass' → credentials; '2 <tok>' → token; '3' → browser;
    anything else (including empty) → skip. Splitting creds is the
    CALLER's side effect (storing them); this never mutates anything."""
    a = str(answer or "").strip()
    head = a.split()[0] if a else "4"
    rest = a[len(head) :].strip()
    if head == "1":
        return AuthChoice("credentials", rest)
    if head == "2":
        return AuthChoice("token", rest)
    if head == "3":
        return AuthChoice("browser", "")
    return AuthChoice("skip", "")


def repair_login_url(url: str) -> str:
    """LENIENT URL REPAIR (live complaint: '127.0.0.1:6000' was rejected):
    scheme bare hosts — private/localhost ranges get http, else https.
    A path the operator typed is preserved."""
    u = str(url or "").strip()
    if not u or "://" in u:
        return u
    priv = re.match(r"(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|localhost)", u)
    return ("http" if priv else "https") + "://" + u
