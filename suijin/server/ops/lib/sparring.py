"""Sparring mode — scheduled blue-vs-red practice with regression scoring.

A spar boots the blue lab, fires a scripted red volley at it, and scores
how well the blue detector caught every attack entry. Results are
compared against a stored baseline; regressions and improvements are
reported. Run it periodically (`suijin spar`) to catch detector drift —
every prompt/scorer change should keep the scoreboard moving up, never
down.

Baselines live in outputs/spar_baselines/<name>.json. Baseline capture:
`suijin spar --save-baseline`. CI-style gate: `suijin spar --fail-on-
regression` exits non-zero when blue's F1 drops below baseline.

Pure-python: the volley is synthetic traffic scored by the REAL blue
detector (anomaly_detector), so a spar needs no live target and stays
offline.
"""

from __future__ import annotations

import json
import time

# (label, entry) — attacks the blue detector MUST catch; benign traffic
# it must NOT flag. Mirrors the eval harness label semantics.
_VOLLEY = [
    *[
        ("attack", {"method": "GET", "path": f"/?id=1 OR 1=1--{n}", "ip": f"10.0.0.{n}", "headers": {}, "body": ""})
        for n in range(1, 4)
    ],
    (
        "attack",
        {"method": "POST", "path": "/search", "ip": "10.0.1.5", "headers": {}, "body": "<script>alert(1)</script>"},
    ),
    ("attack", {"method": "GET", "path": "/../../etc/passwd", "ip": "10.0.1.6", "headers": {}, "body": ""}),
    (
        "attack",
        {
            "method": "GET",
            "path": "/api/v1/user?file=....//....//etc/passwd",
            "ip": "10.0.1.7",
            "headers": {},
            "body": "",
        },
    ),
    ("benign", {"method": "GET", "path": "/", "ip": "192.168.1.10", "headers": {}, "body": ""}),
    ("benign", {"method": "GET", "path": "/about.html", "ip": "192.168.1.11", "headers": {}, "body": ""}),
    (
        "benign",
        {"method": "POST", "path": "/contact", "ip": "192.168.1.12", "headers": {}, "body": "name=alice&msg=hi"},
    ),
    ("benign", {"method": "GET", "path": "/assets/app.js", "ip": "192.168.1.10", "headers": {}, "body": ""}),
    *[
        ("attack", {"method": "GET", "path": "/login?user=admin'--", "ip": "10.0.2.9", "headers": {}, "body": ""}),
        ("attack", {"method": "GET", "path": "/api?query={{7*7}}", "ip": "10.0.2.10", "headers": {}, "body": ""}),
        (
            "benign",
            {"method": "GET", "path": "/products?page=2&sort=price", "ip": "192.168.1.13", "headers": {}, "body": ""},
        ),
    ],
]


def _detector():
    from suijin.modules.blueteam.lib.blue.traffic.anomaly_detector import detect_anomalies

    return detect_anomalies


def _score_volley(threshold: int = 5) -> dict:
    """Fire the volley through the real detector; return label metrics."""
    detect = _detector()
    tp = fp = tn = fn = 0
    misses, false_alarms = [], []
    for label, entry in _VOLLEY:
        signals = detect(entry, {"methods": {entry.get("method", "GET"): 1}, "ips": set(), "avg_body_size": 100})
        score = sum(s[1] for s in signals)
        flagged = score >= threshold
        if label == "attack":
            if flagged:
                tp += 1
            else:
                fn += 1
                misses.append(entry["path"][:60])
        else:
            if flagged:
                fp += 1
                false_alarms.append(entry["path"][:60])
            else:
                tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "threshold": threshold,
        "attacks": tp + fn,
        "benign": tn + fp,
        "caught": tp,
        "missed": fn,
        "false_alarms": fp,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "miss_paths": misses[:6],
        "false_alarm_paths": false_alarms[:6],
    }


def _baseline_dir():
    from suijin.modules.platform.lib.workspace import artifact_dir

    d = artifact_dir("spar_baselines")
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_spar(
    *,
    name: str = "default",
    save_baseline: bool = False,
    fail_on_regression: bool = False,
    threshold: int = 5,
) -> tuple[dict, str]:
    """Run one spar. Returns (result, verdict-line)."""
    result = _score_volley(threshold)
    base_path = _baseline_dir() / f"{name}.json"

    if save_baseline or not base_path.exists():
        base_path.write_text(json.dumps(result, indent=2))
        result["verdict"] = "baseline-saved"
        return result, f"baseline saved ({result['f1']:.2f} F1, {result['caught']}/{result['attacks']} attacks caught)"

    baseline = json.loads(base_path.read_text())
    delta = round(result["f1"] - baseline.get("f1", 0.0), 3)
    if delta < -0.001:
        result["verdict"] = "regression"
        line = (
            f"REGRESSION: F1 {baseline.get('f1', 0):.2f} -> {result['f1']:.2f} "
            f"({result['missed']} attacks missed, {result['false_alarms']} false alarms)"
        )
        if fail_on_regression:
            result["fail"] = True
    elif delta > 0.001:
        result["verdict"] = "improvement"
        line = f"IMPROVED: F1 {baseline.get('f1', 0):.2f} -> {result['f1']:.2f} (consider --save-baseline)"
    else:
        result["verdict"] = "stable"
        line = f"STABLE at F1 {result['f1']:.2f} ({result['caught']}/{result['attacks']} caught, {result['false_alarms']} false alarms)"
    return result, line


def render_spar(result: dict, line: str) -> str:
    return (
        f"Spar verdict : {line}\n"
        f"attacks      : {result['caught']}/{result['attacks']} caught"
        f"{('  missed: ' + ', '.join(result['miss_paths'])) if result['miss_paths'] else ''}\n"
        f"false alarms : {result['false_alarms']}"
        f"{('  on: ' + ', '.join(result['false_alarm_paths'])) if result['false_alarm_paths'] else ''}\n"
        f"precision    : {result['precision']:.2f}   recall: {result['recall']:.2f}   F1: {result['f1']:.2f}"
    )
