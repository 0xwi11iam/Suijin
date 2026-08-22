"""Traffic scorer — 1-10 score per request.

Weights and thresholds come from blue_config.json (scorer.signal_weights,
suspicious_threshold, critical_threshold) — cached by mtime so operator
tuning takes effect live without per-request disk reads. Custom detector
rules (suijin/detector_rules.json) are merged in whenever the operator
has created that file; absent file = no change.
"""

from __future__ import annotations

from suijin.modules.blueteam.lib.blue.traffic.anomaly_detector import detect_anomalies

# legacy config keys -> signal names (fallback when signal_weights omits one)
_LEGACY_KEYS = {
    "sql_injection": "sql_keywords_weight",
    "xss_attempt": "xss_pattern_weight",
    "path_traversal": "path_traversal_weight",
    "new_ip": "new_ip_weight",
    "unusual_method": "unusual_method_weight",
    "body_anomaly": "body_size_anomaly_weight",
}

_CFG_CACHE = {"path": None, "mtime": -1.0, "cfg": {}}


def _scorer_cfg() -> dict:
    try:
        from suijin.modules.blueteam.lib.blue.config import _config_path, load_blue_config

        p = _config_path()
        mtime = p.stat().st_mtime if p.exists() else -1.0
        if _CFG_CACHE["cfg"] is None or _CFG_CACHE["path"] != p or _CFG_CACHE["mtime"] != mtime:
            _CFG_CACHE.update({"path": p, "mtime": mtime, "cfg": (load_blue_config().get("scorer") or {})})
        return _CFG_CACHE["cfg"]
    except Exception:  # noqa: BLE001 — scoring must never crash on config
        return {}


def _weight(cfg: dict, signal: str, default: int) -> int:
    sw = cfg.get("signal_weights") or {}
    if signal in sw:
        try:
            return int(sw[signal])
        except (TypeError, ValueError):
            return default
    legacy = _LEGACY_KEYS.get(signal)
    if legacy and legacy in cfg:
        try:
            return int(cfg[legacy])
        except (TypeError, ValueError):
            return default
    return default


def _custom_rules(request: dict) -> list[tuple[str, int, str]]:
    """(name, weight, detail) signals from operator detector rules, if any."""
    try:
        from suijin.modules.ops.lib.governance import load_rules, match_rules

        if not load_rules():  # absent file = opt-out, zero cost
            return []
        return [(f"rule:{t}", w, "custom detector rule") for t, w in match_rules(request)]
    except Exception:  # noqa: BLE001 — rules must never break scoring
        return []


def score_request(request: dict, profile: dict, attacker_profile=None) -> dict:
    cfg = _scorer_cfg()
    raw = detect_anomalies(request, profile)
    signals = [(name, _weight(cfg, name, w), detail) for name, w, detail in raw]
    signals += _custom_rules(request)
    score = 1
    reasons = []
    for name, weight, detail in signals:
        score = min(10, score + weight)
        reasons.append(f"[{name}] {detail}")
    ip = request.get("ip", "")
    if ip not in profile.get("ips", set()):
        score = min(10, score + _weight(cfg, "new_ip", 2))
        reasons.append("[new_ip] IP never seen on this endpoint")
    body_size = len(str(request.get("body", "")))
    avg_size = profile.get("avg_body_size", 1000)
    if avg_size > 0 and body_size > avg_size * 3:
        score = min(10, score + _weight(cfg, "body_anomaly", 1))
        reasons.append(f"[body_anomaly] Body {body_size} bytes vs avg {avg_size}")
    critical = int(cfg.get("critical_threshold", 8))
    suspicious = int(cfg.get("suspicious_threshold", 5))
    level = "critical" if score >= critical else "suspicious" if score >= suspicious else "noise"
    action = "block" if score >= critical else "validate" if score >= suspicious else "log"
    return {"score": score, "level": level, "action": action, "reasons": reasons, "signals": [s[0] for s in signals]}
