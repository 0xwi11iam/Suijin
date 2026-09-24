"""Stealth — system-wide, ON BY DEFAULT.

The agent must not look like a scanner while it hunts:
  - HTTP: a sticky, realistic browser identity (UA-hopping every request
    is itself a fingerprint) + the full header set browsers send
    (sparse headers = scanner tell)
  - PACING: spacing between requests with human-ish jitter
  - COMMANDS: loud tools get rate caps (nmap -T2 --max-rate, masscan
    --rate, gobuster/ffuf/sqlmap/nikto throttles)

Config: "stealth": true|false in config.json (default true).
Env overrides: SUIJIN_STEALTH=off disables everything;
SUIJIN_STEALTH_PACING=0 disables only the delays (test speed).
"""

from __future__ import annotations

import os
import random
import time as _time

# ── Browser identity pool (current-gen, weighted toward common) ───────────
# No "suijin", no "python-requests", no scanner tokens — these are what
# real traffic looks like. One identity sticks for the process lifetime.
_UA_POOL = (
    # Chrome / Windows (most common combination on the internet)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    # Chrome / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    # Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
)

_STICKY: dict[str, str] = {}


def browser_identity() -> dict[str, str]:
    """Full realistic header set for ONE sticky browser identity.

    The identity is chosen once per process (sticky) so every request
    from this agent looks like the same browser — consistent, boring,
    plausible. Caller-supplied headers always win on merge.
    """
    if "headers" not in _STICKY:
        ua = random.choice(_UA_POOL)  # noqa: S311 - not crypto
        chrome = "Chrome" in ua or "Edg" in ua
        accept = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
            if not chrome
            else "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
        )
        hdrs = {
            "User-Agent": ua,
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9",
            # NO 'br': we advertise only what requests can actually decode.
            # Advertising brotli without the brotli package got us raw
            # binary bodies from Vercel/Cloudflare (field run: spa-target.example).
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        if chrome:
            hdrs["sec-ch-ua"] = '"Chromium";v="131", "Not_A Brand";v="24"'
            hdrs["sec-ch-ua-mobile"] = "?0"
            hdrs["sec-ch-ua-platform"] = '"Windows"' if "Windows" in ua else '"macOS"'
            hdrs["Sec-Fetch-Dest"] = "document"
            hdrs["Sec-Fetch-Mode"] = "navigate"
            hdrs["Sec-Fetch-Site"] = "none"
            hdrs["Sec-Fetch-User"] = "?1"
        else:
            # Firefox (>=90) and Safari both send the Fetch metadata set
            hdrs["Sec-Fetch-Dest"] = "document"
            hdrs["Sec-Fetch-Mode"] = "navigate"
            hdrs["Sec-Fetch-Site"] = "none"
        _STICKY["headers"] = hdrs
    return dict(_STICKY["headers"])


def user_agent() -> str:
    return browser_identity()["User-Agent"]


# ── Pacing ────────────────────────────────────────────────────────────────

_PACE_STATE = {"last": 0.0}
MIN_GAP_S = 0.35  # burst ceiling ≈3 req/s — manual probes (one per LLM
# turn, already seconds apart) NEVER wait; only
# machine-gun bursts get spaced. Performance preserved.


def pacing_enabled() -> bool:
    if os.environ.get("SUIJIN_STEALTH_PACING") == "0":
        return False
    return is_on()


def pacing_wait(now: float | None = None) -> float:
    """Seconds to sleep before the next request (0 = go now).

    Burst limiter, NOT a fixed delay: single probes spaced by the LLM
    loop's own latency (seconds) always return 0 — zero performance
    cost. Only when requests arrive faster than MIN_GAP does the
    spacing kick in, turning a scanner-signature burst into
    human-paced traffic.
    """
    now = _time.monotonic() if now is None else now
    elapsed = now - _PACE_STATE["last"]
    return max(0.0, MIN_GAP_S - elapsed)


def pacing_tick(now: float | None = None) -> None:
    """Record that a request just went out."""
    _PACE_STATE["last"] = _time.monotonic() if now is None else now


def pace() -> None:
    """Blocking pacing: sleep the computed wait, then tick. Caller path
    is already threaded (http_request runs in to_thread)."""
    if not pacing_enabled():
        return
    w = pacing_wait()
    if w > 0:
        _time.sleep(w)
    pacing_tick()


# ── On/off ────────────────────────────────────────────────────────────────


def is_on(config: dict | None = None) -> bool:
    """Stealth is ON unless explicitly disabled (config or env)."""
    if os.environ.get("SUIJIN_STEALTH", "").lower() in ("off", "0", "false", "no"):
        return False
    if config is not None:
        return bool(config.get("stealth", True))
    try:
        from suijin.modules.platform.lib.config_loader import load_config

        return bool(load_config().get("stealth", True))
    except Exception:  # noqa: BLE001 — default stays ON, never breaks
        return True


# ── Command sanitizer — rate-cap the loud tools ───────────────────────────
# argv-form rewrites only (no shell-string mangling); idempotent; benign
# commands pass through untouched; an explicit cap from the operator wins.

_RATE_TOOLS: dict[str, list[str]] = {
    # tool: flags to APPEND when the operator didn't pass their own throttle
    "nmap": ["-T3", "--max-rate", "800"],
    "masscan": ["--rate", "300"],
    "gobuster": ["-t", "8", "--delay", "150ms"],
    "ffuf": ["-rate", "150"],
    "feroxbuster": ["--rate-limit", "150"],
    "sqlmap": ["--delay", "1", "--threads", "3"],
    "nikto": ["-Pause", "1"],
    "dirb": [],
    "dirsearch": ["-t", "5", "--delay", "0.8"],
}
# per-tool tokens that mean "operator already set the pace"
_ALREADY_SET = {
    "nmap": ("-T", "--max-rate", "--min-rate", "--timing"),
    "masscan": ("--rate", "--max-rate"),
    "gobuster": ("-t ", "--delay", "--threads"),
    "ffuf": ("-rate", "-p "),
    "feroxbuster": ("--rate-limit", "--threads", "-t "),
    "sqlmap": ("--delay", "--threads", "-r ", "--time-sec"),
    "nikto": ("-Pause", "-pause"),
    "dirb": (),
    "dirsearch": ("-t ", "--delay", "--threads"),
}


def sanitize_command(argv: list[str], config: dict | None = None) -> list[str]:
    """Return a rate-capped copy of argv when stealth is on.

    Never rewrites: benign commands, unknown tools, already-throttled
    invocations. Idempotent — applying twice equals applying once.
    """
    if not argv or not is_on(config):
        return argv
    tool = os.path.basename(str(argv[0]))
    caps = _RATE_TOOLS.get(tool)
    if caps is None:
        return argv
    joined = " ".join(argv)
    for marker in _ALREADY_SET.get(tool, ()):
        if marker in joined:
            return argv  # operator set their own pace — respect it
    out = list(argv)
    for flag in caps:
        if flag not in out:
            out.append(flag)
    return out
