"""Mode governor — the recon↔exploit mode machinery.

Recon targets, exploitation executes — but the SWITCH itself is now
mechanical, not a matter of model courage:

  - every recon artifact (URL, form, parameter, version match) enters the
    Attack Surface Queue — untried surfaces are visible debt
  - when recon stops producing new surfaces AND untried surfaces remain,
    the governor forces the switch to exploitation mode
  - switching modes swaps the skill doctrine to best-fit the collected
    surfaces (fixes the skill-stuck bug: phase moved, doctrine didn't)
  - the model keeps full discretion to switch anytime; the governor only
    guarantees the floor

Posture dial (config "posture"): "assertive" (default) = short recon fuse;
"recon" = patient. Tone everywhere is procedural inevitability — a tool
change, not an escalation decision.
"""

from __future__ import annotations

import re

# how a surface hints at a payload class — best-fit doctrine selection
_SKILL_HINTS = [
    (re.compile(r"login|signin|auth|session|password", re.I), "sql_injection"),
    (re.compile(r"search|\?q=|query=|&id=|param", re.I), "sql_injection"),
    (re.compile(r"upload|avatar|attachment|file=", re.I), "file_upload"),
    (re.compile(r"webhook|callback|fetch=|url=|redirect", re.I), "ssrf"),
    (re.compile(r"graphql|__schema|introspect", re.I), "graphql"),
    (re.compile(r"jwt|bearer|token", re.I), "jwt"),
]

_CVE_HINT = re.compile(r"\d+\.\d+(\.\d+)?[a-z0-9]*", re.I)


def _cfg_int(cfg: dict, key: str, default: int) -> int:
    try:
        return int((cfg or {}).get(key) or default)
    except Exception:  # noqa: BLE001
        return default


def posture_config(cfg: dict | None) -> dict:
    """ "posture": assertive (default) = 6 pure-recon iterations, 3-iter
    surface stall; recon = patient (20 / 6)."""
    p = str((cfg or {}).get("posture") or "assertive").lower()
    if p == "recon":
        return {"posture": "recon", "recon_cap": 20, "stall": 6, "min_surfaces": 2}
    return {"posture": "assertive", "recon_cap": 6, "stall": 3, "min_surfaces": 2}


def harvest_surfaces(result: dict) -> list[dict]:
    """Pull attack surfaces out of a think/execute result: the tool that
    ran + the args it ran with. Cheap, textual, never raises."""
    out: list[dict] = []
    try:
        step = result.get("_current_step") or {}
        name = str(step.get("tool_name") or "")
        args = step.get("tool_args") or {}
        if name == "http_request":
            url = str(args.get("url") or "")
            if url:
                out.append({"surface": url, "cls": "web", "src": name})
        elif name in ("nmap_scan", "tcp_scan", "recon_chain"):
            out.append({"surface": "port/version map", "cls": "recon", "src": name})
        elif name == "search_cve":
            out.append({"surface": f"cve:{str(args.get('software') or '')[:40]}", "cls": "cve", "src": name})
        for trace_step in (result.get("execution_trace") or [])[-1:]:
            for f in _SURFACE_PATTERNS:
                for m in f[0].finditer(str(trace_step.get("tool_output") or "")[:4000]):
                    out.append({"surface": m.group(0)[:120], "cls": f[1], "src": "output"})
    except Exception:  # noqa: BLE001 — surface tracking must never break a run
        pass
    return out


import re as _re  # noqa: E402 — patterns below

_SURFACE_PATTERNS = [
    (_re.compile(r"<form[^>]*action=[\"']([^\"']+)", _re.I), "form"),
    (_re.compile(r"https?://[^\s'\"<>]+", _re.I), "url"),
]


def update_queue(state: dict, result: dict) -> dict:
    """Merge new surfaces into state['_attack_queue']; mark tried when an
    exploit-class tool targeted them. Returns the updated queue."""
    queue = list(state.get("_attack_queue") or [])
    known = {str(s.get("surface")) for s in queue}
    for s in harvest_surfaces(result):
        key = str(s["surface"])
        if key and key not in known and len(queue) < 60:
            queue.append(
                {"surface": key, "cls": s.get("cls", "?"), "tried": False, "iter": result.get("current_iteration", 0)}
            )
            known.add(key)
    # a confirmed finding on a surface retires it
    for s in queue:
        if not s.get("tried"):
            for tr in (result.get("execution_trace") or [])[-2:]:
                tn = str(tr.get("tool_name") or "")
                if tn in ("catalog_exploit", "record_finding"):
                    s["tried"] = True
    # same-surface grind counter: the repeat-guard catches IDENTICAL calls;
    # same-surface-different-args grinding was unguarded (the field-review
    # loop hole). 4+ attempts without target growth = forced-pivot signal.
    step = result.get("_current_step") or {}
    tgt = str((step.get("tool_args") or {}).get("url") or "")
    if tgt:
        attempts = list(state.get("_surface_attempts") or [])
        attempts.append({"surface": tgt, "iter": result.get("current_iteration", 0),
                         "grew": bool(result.get("_target_grew_last_step"))})
        attempts = attempts[-30:]
        recent = [a for a in attempts if a["surface"] == tgt][-4:]
        result["_surface_attempts"] = attempts
        if len(recent) >= 4 and not any(a["grew"] for a in recent):
            result.setdefault("messages", []).append({
                "role": "user",
                "content": (
                    f"SURFACE STALL: 4+ attempts against {tgt[:70]} with no new target data — this surface is "
                    "confirming dead in its current form. Vary the attack CLASS (payload_mutate family escalation) "
                    "or move to another untried surface; re-testing with near-identical args is the loop failure mode."
                ),
            })
    return queue


def untried(queue: list) -> list[dict]:
    return [s for s in (queue or []) if not s.get("tried")]


# ── foothold engine ──────────────────────────────────────────────────
# One deterministic predicate: shell-ish output, confirmed shell-class
# exploit, or captured credentials ⇒ the engagement has a foothold and
# the governor promotes it to post_exploitation (forced, same mechanism
# as recon → exploitation).
_FOOTHOLD_RX = _re.compile(
    r"uid=\d+\(|\bwhoami\b|shell obtained|reverse shell|webshell|FLAG\{[^}]*rce[^}]*\}",
    _re.I,
)
_SHELL_CLASSES = ("rce", "ssti", "command_injection", "cmdi", "deser", "file_upload", "upload", "privesc")


def update_foothold(state: dict, result: dict) -> bool:
    """Scan a think/execute result for foothold evidence; returns True when
    the foothold flag was just set (first detection wins)."""
    try:
        if state.get("_foothold_at"):
            return False
        texts = []
        step = result.get("_current_step") or {}
        texts.append(str(step.get("tool_output") or ""))
        for tr in (result.get("execution_trace") or [])[-2:]:
            texts.append(str(tr.get("tool_output") or "")[:4000])
        blob = "\n".join(texts)
        hit = bool(_FOOTHOLD_RX.search(blob))
        if not hit and "CONFIRMED" in blob and "catalog" in str(step.get("tool_name") or "") + blob[:200].lower():
            for cls in _SHELL_CLASSES:  # catalog_exploit CONFIRMED on a shell-class entry
                if cls in blob.lower():
                    hit = True
                    break
        if not hit:
            board = (state.get("target_info") or {}).get("credentials") or []
            # cred regexes straight off tool output (UI loot was display-only)
            hit = bool(_CRED_RX.search(blob)) or bool(board)
        if hit:
            result["_foothold_at"] = True
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


_CRED_RX = _re.compile(
    r"\bAKIA[0-9A-Z]{16}\b|\beyJ[A-Za-z0-9_-]{10,}\.|\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bsk-[A-Za-z0-9]{20,}\b"
    r"|password[\"']?\s*[:=]\s*[\"']?\S{6,}",
    _re.I,
)


def govern(state: dict, cfg: dict | None) -> dict | None:
    """The mode switch decision. Returns a state-patch (phase + skill +
    message) or None. Fires when: in informational mode, recon has stalled
    (no NEW surface in `stall` iterations) or hit the pure-recon cap, AND
    untried surfaces ≥ min_surfaces."""
    try:
        pc = posture_config(cfg)
        # FOOTHOLD (forced): in exploitation mode with a foothold, promote
        # to post_exploitation with the doctrine swap — the same proven
        # mechanism as the recon → exploitation switch.
        if str(state.get("current_phase") or "") == "exploitation" and state.get("_foothold_at"):
            if not state.get("_post_exploit_done"):
                return {
                    "current_phase": "post_exploitation",
                    "_just_transitioned_to": "post_exploitation",
                    "attack_path_type": "post_exploit",
                    "_post_exploit_done": True,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "MODE CHANGE → post_exploitation (procedural: foothold established — credentials "
                                "or shell access captured). Doctrine switched to: post_exploit. "
                                "Consolidate: privesc checklist, loot inventory, pivot surface, cleanup."
                            ),
                        }
                    ],
                }
            return None
        if str(state.get("current_phase") or "") != "informational":
            return None
        q = untried(state.get("_attack_queue"))
        if len(q) < pc["min_surfaces"]:
            return None
        iters = int(state.get("current_iteration") or 0)
        since_new = iters - int(
            max((int(s.get("iter") or 0) for s in (state.get("_attack_queue") or [{"iter": 0}])), default=0)
        )
        fresh = any(int(s.get("iter") or 0) >= iters - 1 for s in (state.get("_attack_queue") or []))
        stalled = (not fresh) and since_new >= pc["stall"]
        # the recon cap bounds PATIENCE, not productivity — fresh surfaces
        # always earn more recon; the cap only bites when yield has dried
        over_cap = iters >= pc["recon_cap"] and not fresh and since_new >= 1
        if not (stalled or over_cap):
            return None
        skill = best_skill_for(state)
        top = ", ".join(str(s["surface"])[:50] for s in q[:3])
        msg = (
            f"MODE CHANGE → exploitation (procedural: recon yield exhausted at iteration {iters}, "
            f"{len(q)} untried surfaces queued — top: {top}). Doctrine switched to: {skill}. "
            "Work the queue: test each surface, catalog what lands, note what's dead."
        )
        patch = {
            "current_phase": "exploitation",
            "_just_transitioned_to": "exploitation",
            "attack_path_type": skill,
            "messages": [{"role": "user", "content": msg}],
            "_mode_governor_note": msg,
        }
        return patch
    except Exception:  # noqa: BLE001 — the governor must never break a run
        return None


def best_skill_for(state: dict) -> str:
    """Best-fit doctrine from the collected surfaces."""
    texts = " ".join(str(s.get("surface")) for s in (state.get("_attack_queue") or []))
    texts += " " + " ".join(
        str(t.get("tool_name")) + " " + str(t.get("tool_output"))[:200]
        for t in (state.get("execution_trace") or [])[-8:]
    )
    counts: dict[str, int] = {}
    for rx, skill in _SKILL_HINTS:
        n = len(rx.findall(texts))
        if n:
            counts[skill] = counts.get(skill, 0) + n
    if "cve" in " ".join(s.get("cls", "") for s in (state.get("_attack_queue") or [])):
        counts["cve_exploit"] = counts.get("cve_exploit", 0) + 2
    if not counts:
        return "rce"
    return max(counts.items(), key=lambda kv: kv[1])[0]


def scoreboard(state: dict) -> str:
    """One line for the think context: findings · untried · mode."""
    try:
        q = untried(state.get("_attack_queue"))
        findings = len(state.get("findings") or [])
        phase = str(state.get("current_phase") or "informational")
        skill = str(state.get("attack_path_type") or "targeting")
        fh = " · FOOTHOLD" if state.get("_foothold_at") else ""
        return f"mode={phase} doctrine={skill} · findings={findings} · untried_surfaces={len(q)}{fh}"
    except Exception:  # noqa: BLE001
        return ""
