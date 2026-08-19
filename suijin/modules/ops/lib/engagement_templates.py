"""Engagement templates (C21) — reusable engagement definitions.

A template bundles scope + config + preferred tools/skills so recurring
assessments start identically every time. `suijin engage <name>` applies
one. Stored in the workspace (outputs/engagement_templates/).
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULTS = {
    "external_web": {
        "objective": "Assess the external web presence of {target}",
        "config": {"adversary_profile": "script_kiddie", "max_cost_usd": 10.0},
        "recipes": ["recon_web", "subdomain_sweep"],
        "policy": {"allowed_target_scopes": ["{target_domain}"]},
    },
    "stealth_recon": {
        "objective": "Passive-only reconnaissance of {target}",
        "config": {"adversary_profile": "stealth_apt", "max_cost_usd": 5.0},
        "recipes": ["subdomain_sweep"],
        "policy": {"allowed_target_scopes": ["{target_domain}"], "blocked_tools": ["nmap_scan", "masscan"]},
    },
    "insider_review": {
        "objective": "Review what the provided account can reach at {target}",
        "config": {"adversary_profile": "insider", "max_cost_usd": 8.0},
        "recipes": [],
        "policy": {"allowed_target_scopes": ["internal"]},
    },
}


def _dir() -> Path:
    from suijin.modules.platform.lib.workspace import artifact_dir

    d = artifact_dir("engagement_templates")
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_templates() -> str:
    out = []
    for name, t in sorted(DEFAULTS.items()):
        out.append(f"{name} [built-in]: {t['objective'][:60]}")
    for p in sorted(_dir().glob("*.json")):
        out.append(f"{p.stem} [user]: {p.read_text()[:60]}")
    return "\n".join(out) or "No templates defined."


def save_template(name: str, template: dict) -> str:
    if name in DEFAULTS:
        return f"Error: '{name}' is built-in — pick another name"
    (_dir() / f"{name}.json").write_text(json.dumps(template, indent=2))
    return f"saved template '{name}'"


def apply_template(name: str, target: str) -> dict:
    """Resolve a template for a concrete target -> engagement config."""
    if name in DEFAULTS:
        t = DEFAULTS[name]
    else:
        p = _dir() / f"{name}.json"
        if not p.exists():
            raise FileNotFoundError(f"no template '{name}' (list_templates shows what exists)")
        t = json.loads(p.read_text())
    domain = target.split("//")[-1].split("/")[0]
    resolved = json.loads(json.dumps(t).replace("{target_domain}", domain).replace("{target}", target))
    resolved["target"] = target
    return resolved


# ── C22: scheduling ────────────────────────────────────────────────────


def schedule_engagement(template_name: str, cron_expr: str, target: str, install: bool = False) -> dict:
    """Generate (and optionally install) a cron entry running an
    engagement from a template on a schedule. Returns the crontab line.

    We do NOT ship a daemon: the system scheduler runs the CLI. install=True
    appends to the user crontab (idempotent via a marker comment)."""
    import subprocess

    resolved = apply_template(template_name, target)
    marker = f"# suijin:{template_name}:{target}"
    cmd = f'suijin run --template {template_name} --target "{target}" >> "$HOME/.suijin/schedule.log" 2>&1'
    line = f"{cron_expr} {cmd}"
    entry = f"{marker}\n{line}"
    if install:
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        if marker in current:
            return {"installed": True, "already": True, "entry": entry}
        new = current.rstrip() + "\n" + entry + "\n"
        proc = subprocess.run(["crontab", "-"], input=new, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"crontab install failed: {proc.stderr[:120]}")
    return {"installed": bool(install), "entry": entry, "objective": resolved["objective"]}


def unschedule_engagement(template_name: str, target: str) -> bool:
    import subprocess

    marker = f"# suijin:{template_name}:{target}"
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    if marker not in current:
        return False
    lines = current.splitlines()
    out, skip = [], False
    for ln in lines:
        if ln == marker:
            skip = True
            continue
        if skip:
            skip = False
            continue
        out.append(ln)
    proc = subprocess.run(["crontab", "-"], input="\n".join(out) + "\n", capture_output=True, text=True)
    return proc.returncode == 0
