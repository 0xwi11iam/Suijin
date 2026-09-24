"""Cost governor — the hard per-engagement spend kill switch.

The warn path tells the agent to wrap up; the hard stop ends the run
regardless of iteration budget. Budget comes from config.json:

    "max_cost_usd": 5.0     # hard stop (default 25.0)
    "cost_warn_pct": 80     # warn threshold as % of max (default 80)

Never raises into the caller; an unpriced tally (USAGE["priced"] False)
only warns — an unpriceable model must not brick the run.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("suijin.governor")

DEFAULT_MAX_COST_USD = 25.0
DEFAULT_WARN_PCT = 80


def _usage() -> dict:
    from suijin.modules.providers.lib import USAGE

    return USAGE


def budget_status(config: dict | None) -> dict:
    """One budget snapshot: {limit, spent, pct, action}.

    action: 'ok' | 'warn' | 'stop' — 'stop' means end the engagement now.
    """
    cfg = config or {}
    # explicit 0 / negative = UNLIMITED (operator: no spending caps).
    # `cfg.get("max_cost_usd") or DEFAULT` swallowed 0 back to the default —
    # a set-to-zero cap was silently a $25 stop.
    limit = float(cfg.get("max_cost_usd") or 0.0) if "max_cost_usd" in cfg else DEFAULT_MAX_COST_USD
    if limit <= 0:
        return {"limit": 0.0, "spent": _usage().get("est_cost_usd", 0.0), "pct": 0.0, "priced": False, "action": "ok"}
    warn_pct = float(cfg.get("cost_warn_pct") or DEFAULT_WARN_PCT)
    usage = _usage()
    spent = float(usage.get("est_cost_usd", 0.0))
    priced = bool(usage.get("priced", False))
    pct = (spent / limit * 100.0) if limit > 0 else 0.0
    action = "ok"
    if priced and spent >= limit:
        action = "stop"
    elif priced and pct >= warn_pct:
        action = "warn"
    return {"limit": limit, "spent": spent, "pct": round(pct, 1), "priced": priced, "action": action}


def budget_guard(config: dict | None = None) -> str | None:
    """Check-and-advise for the agent loop. Returns None when fine,
    a guidance string on 'warn', and a stop directive on 'stop'.

    The stop directive is parsed by the think node as a forced completion.
    """
    st = budget_status(config)
    if st["action"] == "ok":
        return None
    if st["action"] == "warn":
        msg = (
            f"COST WARN: ${st['spent']:.2f} of ${st['limit']:.2f} budget used ({st['pct']:.0f}%). "
            "Wrap up remaining verification efficiently and finalize findings."
        )
        logger.warning(msg)
        return msg
    msg = (
        f"COST LIMIT REACHED: ${st['spent']:.2f} >= ${st['limit']:.2f} hard budget. "
        "The engagement is being stopped by the cost governor. Summarize results achieved so far."
    )
    logger.warning(msg)
    return msg
