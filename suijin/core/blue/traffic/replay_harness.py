"""Detector replay & tuning harness — evaluate the blue traffic pipeline
offline against recorded traffic, with labeled ground truth.

`suijin blue eval` replays a traffic log through the REAL scorer
(core/blue/traffic/scorer.py), labels each entry (heuristic attack/benign
rules, or a labels.jsonl file), and reports precision / recall / F1 per
threshold plus a sweep to find the best operating point. Offline, no keys.
"""

from __future__ import annotations

import json
from pathlib import Path

from suijin.core.blue.traffic.scorer import score_request

# ── Ground-truth labeling ───────────────────────────────────────────────
# Strong, low-false-positive attack substrings checked against method+path+
# body+user_agent+headers. Unlabeled entries become "unknown" and are
# excluded from metrics (never silently counted as benign).
HEURISTIC_ATTACK_RULES: list[tuple[str, list[str]]] = [
    ("sqli", ["' or '1'='1", "union select", "union all select", "'; drop table", "' --", "sleep(", "benchmark("]),
    ("xss", ["<script", "onerror=", "javascript:", "<svg/onload", "<img src=x"]),
    ("traversal", ["../../", "..\\..", "/etc/passwd", "/etc/shadow"]),
    ("ssti", ["{{7*7}}", "${7*7}", "{{__import__", "${jndi:"]),
    ("xxe", ["<!entity", "<!doctype foo ["]),
    ("file_inclusion", ["php://filter", "php://input", "data://text"]),
    ("mass_assignment", ['"role":"admin"', '"role": "admin"', '"is_admin":true']),
    ("auth_bypass", ["x-admin: true", "x-role: admin"]),
    ("scanner_ua", ["sqlmap", "nikto", "nmap scripting", "gobuster", "masscan", "hydra"]),
    ("nosql", ['"$ne"', '"$gt"', '"$regex"']),
    ("graphql_attack", ["__schema {", "__schema{"]),
]


def _entry_text(entry: dict) -> str:
    parts = [
        str(entry.get("path", "")),
        str(entry.get("query", "")),
        str(entry.get("body", "")),
        str(entry.get("user_agent", "")),
        json.dumps(entry.get("headers", {})).lower(),
    ]
    return " ".join(parts).lower()


def label_heuristic(entry: dict) -> tuple[str, str]:
    """Returns (label, attack_type) — label is attack|benign|unknown."""
    text = _entry_text(entry)
    for atk_type, needles in HEURISTIC_ATTACK_RULES:
        if any(n in text for n in needles):
            return "attack", atk_type
    method = str(entry.get("method", "GET")).upper()
    path = str(entry.get("path", "/"))
    clean_paths = ("/", "/health", "/auth/login", "/auth/me", "/api/users", "/api/search", "/api/coupons")
    clean_body = str(entry.get("body", "") or "")
    simple_get = method == "GET" and (
        path in clean_paths or path.isascii() and " " not in path and ".." not in path and path.count("/") <= 2
    )
    if simple_get and len(clean_body) == 0:
        return "benign", ""
    if (
        method == "POST"
        and path in ("/auth/login", "/auth/register")
        and '"role"' not in clean_body
        and "'" not in clean_body
        and "<" not in clean_body
    ):
        return "benign", ""
    return "unknown", ""


def load_labels(path: Path) -> list[dict]:
    """labels.jsonl: {"label": "attack"|"benign", "any": [substr...]} rules,
    first match wins; entries matching nothing stay heuristic-labeled."""
    rules = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            try:
                rules.append(json.loads(line))
            except ValueError:
                continue
    return rules


def label_entries(entries: list[dict], labels_path: Path | None = None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    rules = load_labels(labels_path) if labels_path else []
    for e in entries:
        text = _entry_text(e)
        labeled = None
        for rule in rules:
            if any(n in text for n in rule.get("any", [])):
                labeled = (rule["label"], rule.get("type", "custom"))
                break
        out.append(labeled or label_heuristic(e))
    return out


# ── Replay & metrics ────────────────────────────────────────────────────


def replay_scores(entries: list[dict], baseline: int = 15) -> list[dict]:
    """Run every entry through the real scorer with a light baseline profile
    (mirrors SmartNormalizer's role: known methods/IPs/avg body size)."""
    base = entries[:baseline]
    methods: dict[str, int] = {}
    sizes: list[int] = []
    ips: set[str] = set()
    for e in base:
        m = str(e.get("method", "GET"))
        methods[m] = methods.get(m, 0) + 1
        ips.add(str(e.get("ip", "")))
        sizes.append(len(str(e.get("body", ""))))
    profile = {
        "methods": methods,
        "ips": ips,
        "avg_body_size": (sum(sizes) / len(sizes)) if sizes else 1000,
    }
    return [score_request(e, profile) for e in entries]


def metrics(labels: list[tuple[str, str]], scores: list[dict], threshold: int) -> dict:
    """Confusion matrix + precision/recall/F1 at a score threshold."""
    tp = fp = tn = fn = skipped = 0
    for (label, _atk), sc in zip(labels, scores, strict=False):
        if label == "unknown":
            skipped += 1
            continue
        predicted_attack = sc["score"] >= threshold
        if label == "attack" and predicted_attack:
            tp += 1
        elif label == "benign" and predicted_attack:
            fp += 1
        elif label == "benign" and not predicted_attack:
            tn += 1
        else:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "threshold": threshold,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "skipped_unknown": skipped,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def sweep(labels: list[tuple[str, str]], scores: list[dict], lo: int = 2, hi: int = 9) -> list[dict]:
    return [metrics(labels, scores, t) for t in range(lo, hi + 1)]


def best_threshold(rows: list[dict]) -> dict:
    return max(rows, key=lambda r: r["f1"])


def render_eval(
    labels: list[tuple[str, str]], scores: list[dict], default_threshold: int = 5, do_sweep: bool = True
) -> str:
    attacks = sum(1 for lab, _ in labels if lab == "attack")
    benign = sum(1 for lab, _ in labels if lab == "benign")
    unknown = len(labels) - attacks - benign
    lines = [
        f"traffic entries: {len(labels)}  (attack {attacks} / benign {benign} / unlabeled {unknown})",
        "",
    ]
    m = metrics(labels, scores, default_threshold)
    lines.append(
        f"@ threshold {default_threshold} (production default):  "
        f"P {m['precision']:.2f}  R {m['recall']:.2f}  F1 {m['f1']:.2f}  "
        f"(TP {m['tp']} FP {m['fp']} TN {m['tn']} FN {m['fn']})"
    )
    if do_sweep:
        lines.append("")
        lines.append("  thr    prec  rec   F1    TP FP TN FN")
        rows = sweep(labels, scores)
        for r in rows:
            mark = "*" if r["threshold"] == default_threshold else " "
            lines.append(
                f"  {r['threshold']:>2}{mark}  {r['precision']:.2f}  {r['recall']:.2f}  "
                f"{r['f1']:.2f}  {r['tp']:>2} {r['fp']:>2} {r['tn']:>2} {r['fn']:>2}"
            )
        best = best_threshold(rows)
        lines.append("")
        lines.append(
            f"  best F1 at threshold {best['threshold']} "
            f"(P {best['precision']:.2f} R {best['recall']:.2f} F1 {best['f1']:.2f}) — "
            f"tune via blue_config.json scorer.suspicious_threshold"
        )
    if unknown:
        lines.append("")
        lines.append(
            f"  note: {unknown} unlabeled entries excluded from metrics — provide labels.jsonl rules for full coverage"
        )
    return "\n".join(lines)
