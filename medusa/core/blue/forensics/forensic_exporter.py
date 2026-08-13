"""Forensic exporter — export timeline as JSON/CSV."""
from __future__ import annotations
import json, csv, io

def export_json(timeline: list) -> str:
    return json.dumps(timeline, indent=2, default=str)

def export_csv(timeline: list) -> str:
    out = io.StringIO()
    if timeline:
        w = csv.DictWriter(out, fieldnames=timeline[0].keys())
        w.writeheader(); w.writerows(timeline)
    return out.getvalue()
