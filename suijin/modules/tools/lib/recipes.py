"""Tool recipes — named multi-tool macros (A3) + mining from history (A4).

A recipe is an ordered list of steps: {tool, args (with {target} templating),
expect (optional marker checked with 'in')}. Built-ins cover common flows;
operators/agent define more (workspace recipes.json); the miner (A4)
discovers repeated successful sequences from engagement audit trails and
proposes them as recipes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("suijin.recipes")

BUILT_IN_RECIPES: dict[str, list[dict]] = {
    "recon_web": [
        {"tool": "whatweb_scan", "args": {"url": "{target}"}},
        {"tool": "extract_links", "args": {"html": "{prev}", "base_url": "{target}"}, "optional": True},
        {"tool": "analyze_robots", "args": {"content": "{robots}", "base_url": "{target}"}, "optional": True},
        {"tool": "audit_security_headers", "args": {"headers": "{headers}"}, "optional": True},
    ],
    "subdomain_sweep": [
        {"tool": "crtsh_subdomains", "args": {"domain": "{domain}"}},
        {"tool": "dns_brute", "args": {"domain": "{domain}"}},
    ],
    "email_recon": [
        {"tool": "email_security_records", "args": {"domain": "{domain}"}},
        {"tool": "spf_audit", "args": {"domain": "{domain}"}},
        {"tool": "harvest_emails", "args": {"url": "{target}"}},
    ],
}


def _user_store() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    p = WORKSPACE_DIR / "recipes.json"
    if not p.exists():
        p.write_text("{}")
    return p


def _load_user() -> dict:
    try:
        data = json.loads(_user_store().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def recipe_list() -> str:
    out = []
    for name, steps in sorted(BUILT_IN_RECIPES.items()):
        out.append(f"{name} [built-in]: " + " -> ".join(s["tool"] for s in steps))
    for name, steps in sorted(_load_user().items()):
        out.append(f"{name} [user]: " + " -> ".join(s.get("tool", "?") for s in steps if isinstance(s, dict)))
    return "\n".join(out) or "No recipes defined."


def recipe_define(name: str = "", steps_json: str = "") -> str:
    """Define a user recipe from a JSON list of steps."""
    if not name or not steps_json:
        return "Error: name and steps_json required (JSON list of {tool, args})"
    try:
        steps = json.loads(steps_json)
    except ValueError as e:
        return f"Error: steps_json not valid JSON: {e}"
    if not isinstance(steps, list) or not steps or not all(isinstance(s, dict) and "tool" in s for s in steps):
        return "Error: steps must be a non-empty JSON list of {tool, args} objects"
    if name in BUILT_IN_RECIPES:
        return f"Error: '{name}' is a built-in recipe — pick another name"
    store = _user_store()
    data = _load_user()
    data[name] = steps
    store.write_text(json.dumps(data, indent=2))
    return f"defined recipe '{name}' ({len(steps)} steps)"


def recipe_run(name: str = "", target: str = "", route_fn=None) -> str:
    """Execute a recipe against a target. Each step's args get
    {target}/{domain} templating; a failing non-optional step aborts."""
    if not name or not target:
        return "Error: name and target required"
    steps = BUILT_IN_RECIPES.get(name) or _load_user().get(name)
    if not steps:
        return f"Error: no recipe '{name}' (recipe_list shows what exists)"
    if route_fn is None:
        from suijin.modules.tools.lib.dispatch import route_tool

        route_fn = route_tool
    domain = target.split("//")[-1].split("/")[0].split(":")[0]
    results = []
    prev_out = ""
    for i, step in enumerate(steps, 1):
        args = {}
        for k, v in (step.get("args") or {}).items():
            val = str(v)
            if "{target}" in val:
                val = val.replace("{target}", target)
            if "{domain}" in val:
                val = val.replace("{domain}", domain)
            if val == "{prev}":
                val = prev_out[:4000]
            args[k] = val
        out = str(route_fn(step["tool"], args, {}) or "")
        ok_step = not out.startswith("Error")
        results.append(f"[{i}/{len(steps)}] {step['tool']}: {'OK' if ok_step else 'FAILED'}\n{out[:500]}")
        if not ok_step:
            if step.get("optional"):
                continue
            return f"recipe '{name}' aborted at step {i} ({step['tool']}):\n" + "\n".join(results)
        prev_out = out
    return f"recipe '{name}' complete ({len(steps)} steps):\n" + "\n".join(results)


def mine_recipes(min_support: int = 2, min_len: int = 3, max_recipes: int = 5) -> str:
    """A4: discover repeated successful tool sequences in engagement
    history and propose them as recipes."""
    from collections import Counter

    from suijin.modules.platform.lib.workspace import artifact_dir

    seqs: list[tuple[str, ...]] = []
    trails = artifact_dir("audit_trails")
    if not trails.is_dir():
        return "No audit trails to mine."
    for p in sorted(trails.glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        tools = [it.get("tool", "") for it in (d.get("iterations") or []) if it.get("success") and it.get("tool")]
        # n-grams of successful tool runs, len in [min_len, min_len+2]
        for n in range(min_len, min_len + 3):
            for i in range(len(tools) - n + 1):
                seqs.append(tuple(tools[i : i + n]))
    if not seqs:
        return "No successful tool sequences in history yet."
    counts = Counter(seqs)
    proposals = []
    for seq, n in counts.most_common():
        if n < min_support or len(proposals) >= max_recipes:
            break
        if any(seq == tuple(p) for p in proposals):
            continue
        proposals.append(seq)
    if not proposals:
        return f"No sequence repeated >= {min_support}x yet — keep engaging."
    out = []
    for i, seq in enumerate(proposals, 1):
        steps = json.dumps([{"tool": t, "args": {}} for t in seq])
        out.append(f"proposal {i} (seen {counts[seq]}x): {' -> '.join(seq)}")
        out.append(f"  adopt: recipe_define('mined_{i}', '{steps[:120]}...')")
    return "\n".join(out)
