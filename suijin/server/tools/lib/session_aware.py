"""
suijin/tools/session_aware.py — Session tracking, rate limit awareness, stealth mode.

Provides:
- SessionState: track cookies, CSRF tokens, auth state across requests
- RateLimitTracker: detect 429s, respect Retry-After, auto-throttle
- Stealth mode: random User-Agent rotation, jitter, traffic shaping
"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

# ── Session tracking ──────────────────────────────────────────────────────────


class SessionState:
    """Track a single target's session (cookies, tokens, auth state)."""

    def __init__(self, target: str):
        self.target = target
        self.cookies: dict = {}
        self.csrf_tokens: dict = {}
        self.auth_header: Optional[str] = None
        self.last_request_at: Optional[datetime] = None
        self.request_count: int = 0

    def update_from_response(self, headers: dict, body: str = ""):
        """Extract cookies and CSRF tokens from response headers/body."""
        import re

        # Extract Set-Cookie — each header carries ONE cookie; everything
        # after the first ';' is an attribute (Path/Domain/Expires/...),
        # not a cookie to replay. Attributes were previously parsed as
        # cookies and leaked into subsequent requests.
        for header_key, header_val in headers.items():
            if header_key.lower() == "set-cookie":
                first = str(header_val).split(";", 1)[0]
                if "=" in first:
                    k, v = first.strip().split("=", 1)
                    if k.strip():
                        self.cookies[k.strip()] = v.strip()
        # Common CSRF token patterns in body
        csrf_patterns = [
            r'name="(?:csrf|authenticity_token|_token|csrf_token|csrfmiddlewaretoken)"[^>]*value="([^"]+)"',
            r'name="(?:csrf|authenticity_token|_token|csrf_token|csrfmiddlewaretoken)"[^>]*value=\'([^\']+)\'',
        ]
        for pattern in csrf_patterns:
            for match in re.finditer(pattern, body, re.IGNORECASE):
                self.csrf_tokens["form_token"] = match.group(1)

    def get_cookie_string(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def is_authenticated(self) -> bool:
        return bool(self.auth_header) or any(k.lower() in ("session", "token", "auth", "jwt") for k in self.cookies)

    def touch(self):
        self.last_request_at = datetime.now(timezone.utc)
        self.request_count += 1


# ── Rate limit awareness ──────────────────────────────────────────────────────


class RateLimitTracker:
    """Track rate limits per target domain and auto-throttle."""

    def __init__(self):
        self._lock = threading.Lock()
        self._domains: dict = {}  # domain -> {"limit": int, "remaining": int, "reset_at": float, "retry_after": float}

    def update(self, url: str, status_code: int, headers: dict = None):
        """Update rate limit state from a response."""
        domain = urlparse(url).netloc or url
        with self._lock:
            if domain not in self._domains:
                self._domains[domain] = {"limit": 1000, "remaining": 1000, "reset_at": 0, "retry_after": 0}
            entry = self._domains[domain]
            if status_code == 429:
                entry["retry_after"] = float(headers.get("Retry-After", 10)) if headers else 10
                entry["reset_at"] = time.time() + entry["retry_after"]
            if headers:
                entry["remaining"] = int(headers.get("X-RateLimit-Remaining", entry["remaining"]))
                entry["limit"] = int(headers.get("X-RateLimit-Limit", entry["limit"]))

    def should_throttle(self, url: str) -> float:
        """Return seconds to wait before next request, or 0 if ready."""
        domain = urlparse(url).netloc or url
        with self._lock:
            entry = self._domains.get(domain, {})
            if entry.get("remaining", 1000) < 5:
                wait = max(0, entry.get("reset_at", 0) - time.time())
                return wait if wait > 0 else 1.0
            if entry.get("retry_after", 0) > 0:
                wait = max(0, entry.get("reset_at", 0) - time.time())
                return wait
        return 0.0

    def is_blocked(self, url: str) -> bool:
        return self.should_throttle(url) > 2.0


# ── Stealth mode ──────────────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
]

_last_ua_idx = -1
_rate_tracker = RateLimitTracker()
_sessions: dict = {}  # target -> SessionState


def get_random_ua() -> str:
    global _last_ua_idx
    idx = random.randint(0, len(USER_AGENTS) - 1)
    while idx == _last_ua_idx and len(USER_AGENTS) > 1:
        idx = random.randint(0, len(USER_AGENTS) - 1)
    _last_ua_idx = idx
    return USER_AGENTS[idx]


def jitter(base_delay: float = 1.0, jitter_factor: float = 0.5) -> float:
    """Return a jittered delay: base ± jitter_factor*base."""
    return base_delay + random.uniform(-jitter_factor * base_delay, jitter_factor * base_delay)


def get_session(target: str) -> SessionState:
    if target not in _sessions:
        _sessions[target] = SessionState(target)
    return _sessions[target]


def is_rate_limited(url: str) -> bool:
    return _rate_tracker.is_blocked(url)


def record_response(url: str, status_code: int, headers: dict = None):
    _rate_tracker.update(url, status_code, headers)
