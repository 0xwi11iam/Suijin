"""Fireteam sub-agents — parallel specialists that actually work.

v5.1 rebuild (the subagent was "always a bit cooked"):

  B1 mechanics — the old limits guaranteed failure:
     steps 3 -> 12 (config subagent_max_steps), shared 60s batch
     guillotine -> per-subagent budget (default 300s), tool output
     800 -> 4000 chars (a tester must see its own evidence), circuit
     breaker at 3 failures.
  B2 vision — the old prompt hardcoded 8 tools while 262 existed.
     Subagents now see the SAME kernel-rendered tool reference as the
     main agent (live registry, arg names included).
  B3 fireteam — deploy_subagent no longer blocks the main loop:
     teams run as background asyncio tasks, results arrive as messages
     on future turns, fireteam_status() polls progress.
  Parsing — the old bespoke regex could not match nested tool_args;
     subagents reuse the main agent's tolerant decision parser
     (try_parse_llm_decision) and the same minimal decision format.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Tunables (config keys: subagent_max_steps / subagent_timeout_s) ────────
LLM_TIMEOUT = 60  # seconds per subagent LLM call (was 15, then 30 — field
# runs still showed timeouts on slow providers; config: subagent_llm_timeout_s,
# env: SUIJIN_SUBAGENT_LLM_TIMEOUT). One timeout triggers a single retry
# before it counts as a failure (three strikes still auto-stop).
TOOL_TIMEOUT = 60  # seconds per tool execution (was 30)
MAX_SUBAGENT_STEPS = 12  # was 3 — a specialist needs recon->test->verify->report
SUBAGENT_TIMEOUT = 300  # per-subagent budget (was 60s for the WHOLE batch)
MAX_TEAM_SIZE = 5


def _llm_timeout() -> int:
    """Effective per-call timeout: env > config > default."""
    import os

    env = os.environ.get("SUIJIN_SUBAGENT_LLM_TIMEOUT")
    if env:
        try:
            return max(5, int(env))
        except ValueError:
            pass
    try:
        from suijin.modules.tools.lib.services import get as _service

        cfg = _service("red_config") or {}
        v = cfg.get("subagent_llm_timeout_s")
        if v:
            return max(5, int(v))
    except Exception:  # noqa: BLE001 — tuning must never break a subagent
        pass
    return LLM_TIMEOUT


# ── Usefulness gates: a wasted specialist is worse than none ──────────────
# Deploy-time: vague or duplicate tasks are rejected with a REASON the
# main agent can act on. Collection-time: findings are compressed to
# evidence lines (see _compress_findings). Poll-time: status nudges the
# agent back to work when nothing changed.

_RECENT_TASKS: dict[str, float] = {}  # normalized task -> deploy monotonic time
_TASK_TTL = 3600.0  # forget duplicates after an hour


def _norm_task(task: str) -> str:
    return " ".join(task.lower().split())


def _gate_task(task: str) -> str | None:
    """Reject vague/duplicate tasks. Returns the reason, or None if good."""
    t = task.strip()
    if len(t) < 15 or len(t.split()) < 3:
        return "too vague — describe the target AND what to test (one concrete task)"
    now = time.monotonic()
    # expire old entries
    for k in [k for k, ts in _RECENT_TASKS.items() if now - ts > _TASK_TTL]:
        _RECENT_TASKS.pop(k, None)
    if _norm_task(t) in _RECENT_TASKS:
        return "duplicate — this exact task was already deployed recently (check its result or rephrase)"
    return None


_EVIDENCE_MARKERS = (
    "200",
    "201",
    "301",
    "302",
    "401",
    "403",
    "500",
    "found",
    "confirm",
    "vuln",
    "error",
    "fail",
    "flag{",
    "admin",
    "root",
    "password",
    "secret",
    "key",
    "token",
    "sqli",
    "xss",
    "rce",
    "ssrf",
    "exploit",
    "version",
)


def _compress_findings(parts: list[str], keep: int = 8) -> tuple[str, bool]:
    """Findings -> (compressed, low_evidence). Evidence lines first
    (status codes, confirmations, creds, vuln classes); the COMPLETE
    verdict always leads; near-empty evidence is flagged so the main
    agent treats the result with skepticism instead of dumping noise."""
    complete = next((p for p in parts if p.startswith("[COMPLETE]")), "")
    tool_lines = [p for p in parts if p.startswith("[") and not p.startswith(("[COMPLETE]", "[step", "[budget"))]
    evidence = [ln for ln in tool_lines if any(m in ln.lower() for m in _EVIDENCE_MARKERS)]
    plain = [ln for ln in tool_lines if ln not in evidence]
    kept = evidence[:keep] if evidence else plain[:3]  # no evidence? at least show *something*
    low = len(evidence) < 2
    out = "\n".join(([complete] if complete else []) + kept)
    return out, low


@dataclass
class SubagentResult:
    """Result from a completed subagent."""

    subagent_id: str
    task: str
    success: bool
    findings: str
    steps: int
    partial: bool = False
    completed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def summary_line(self) -> str:
        status = "OK" if self.success else ("TIMEOUT" if self.partial else "PARTIAL")
        return f"[{status}] {self.task[:80]} ({self.steps} steps)"


def _tool_reference_text(route_tool_fn=None) -> str:
    """The LIVE tool registry — same surface the main agent sees. When the
    spawn came from a BLUE graph (route_tool_fn is the blue router), the
    prompt must advertise the BLUE arsenal — a red registry with a blue
    router is the prompt/router mismatch the BF2 audit flagged."""
    try:
        mod = getattr(route_tool_fn, "__module__", "")
        if "blueteam" in mod:
            from suijin.modules.blueteam.lib.blue.tools import render_blue_tools

            return render_blue_tools()
    except Exception:  # noqa: BLE001 — never block a spawn on rendering
        pass
    try:
        from suijin.kernel.controller import last_context

        ctx = last_context()
        if ctx is not None:
            return ctx.tool_reference(core_first=("tools", "platform", "knowledge", "providers"))
    except Exception:  # noqa: BLE001 — never block a spawn on rendering
        pass
    try:
        from suijin.modules.tools.lib.dispatch import _manifest_reference

        return _manifest_reference()
    except Exception:  # noqa: BLE001
        return "(tool reference unavailable)"


def _build_system_prompt(task: str, max_steps: int, route_tool_fn=None) -> str:
    return f"""# ROLE: Specialist Subagent — ONE task, done well.

## TASK
{task}

## RULES
1. Focus ONLY on this task. No scope creep, no unrelated recon.
2. You have {max_steps} steps MAXIMUM. Be direct and evidence-driven.
3. Report what worked, what failed, and what you discovered — with concrete output.
4. Done OR genuinely stuck after trying different approaches? action="complete" immediately.
5. A tool failing 3 times in a row means STOP and report — do not retry a fourth time.

## TOOLS — the same registry the main agent uses (name(args) — what it does):
{_tool_reference_text(route_tool_fn)}

## DECISION FORMAT — exactly ONE JSON object per turn (same as the main agent):
{{"action": "use_tool", "tool_name": "...", "tool_args": {{...}}, "thought": "one line"}}
{{"action": "complete", "completion_reason": "what you found", "thought": "..."}}
"""


async def run_subagent(
    task: str,
    *,
    generate_fn,
    route_tool_fn,
    tool_catalog_fn=None,  # retained for API compat; vision comes from the kernel
    max_steps: int | None = None,
    budget_s: float | None = None,
) -> SubagentResult:
    """Run one focused subagent: tight think->execute loop, tolerant parsing."""
    from suijin.modules.platform.lib.helpers.parsing import try_parse_llm_decision

    subagent_id = uuid.uuid4().hex[:8]
    max_steps = max_steps or MAX_SUBAGENT_STEPS
    budget_s = budget_s or SUBAGENT_TIMEOUT
    deadline = time.monotonic() + budget_s
    logger.info("Subagent [%s] start: %s", subagent_id, task[:100])

    messages = [
        {"role": "system", "content": _build_system_prompt(task, max_steps, route_tool_fn)},
        {"role": "user", "content": f"Execute this task now: {task}"},
    ]

    findings_parts: list[str] = []
    success = False
    consecutive_failures = 0
    step_num = 0
    partial = False

    for step_num in range(1, max_steps + 1):
        if time.monotonic() > deadline:
            findings_parts.append(f"[budget] exceeded {budget_s:.0f}s budget after {step_num - 1} steps")
            partial = True
            break
        if consecutive_failures >= 3:
            findings_parts.append("Auto-stopped after 3 consecutive failures.")
            break

        try:
            timeout = _llm_timeout()
            try:
                response = await asyncio.wait_for(generate_fn(messages, {}), timeout=timeout)
            except asyncio.TimeoutError:
                # one patient retry — slow providers (big prompts, cold
                # caches) commonly blow a single tight window
                try:
                    response = await asyncio.wait_for(generate_fn(messages, {}), timeout=timeout)
                except asyncio.TimeoutError:
                    findings_parts.append(f"[step {step_num}] LLM timed out ({timeout}s, retried once)")
                    consecutive_failures += 1
                    continue
            except Exception as e:  # noqa: BLE001 — provider errors are data
                findings_parts.append(f"[step {step_num}] LLM error: {e}")
                consecutive_failures += 1
                continue

            messages.append({"role": "assistant", "content": str(response)})
            consecutive_failures = 0

            decision, parse_error = try_parse_llm_decision(str(response))
            if decision is None:
                findings_parts.append(f"[step {step_num}] unparseable: {str(response)[:120]}")
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Unparseable ({parse_error}). Respond with EXACTLY ONE JSON object:\n"
                            '{"action": "use_tool", "tool_name": "<tool>", "tool_args": {...}, "thought": "..."}'
                        ),
                    }
                )
                consecutive_failures += 1
                continue

            action = decision.get("action", "")

            if action == "complete":
                reason = decision.get("completion_reason", decision.get("thought", "Task done"))
                findings_parts.append(f"[COMPLETE] {reason}")
                success = True
                break

            if action == "use_tool":
                tool_name = decision.get("tool_name", "")
                tool_args = decision.get("tool_args") or {}
                if not tool_name:
                    messages.append(
                        {"role": "user", "content": "Missing tool_name — pick one from the tool reference."}
                    )
                    consecutive_failures += 1
                    continue
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(route_tool_fn, tool_name, tool_args, {}),
                        timeout=TOOL_TIMEOUT,
                    )
                    result_str = str(result)[:4000]  # a tester must SEE its evidence
                    findings_parts.append(f"[{tool_name}] {result_str[:800]}")
                    messages.append({"role": "user", "content": f"Tool output:\n{result_str}"})
                    consecutive_failures = 0
                except asyncio.TimeoutError:
                    findings_parts.append(f"[{tool_name}] timed out ({TOOL_TIMEOUT}s)")
                    messages.append(
                        {"role": "user", "content": f"Tool {tool_name} timed out. Try a simpler call or complete."}
                    )
                    consecutive_failures += 1
                except Exception as e:  # noqa: BLE001
                    messages.append(
                        {
                            "role": "user",
                            "content": f"Tool {tool_name} error: {e}. Try a different approach or complete.",
                        }
                    )
                    consecutive_failures += 1
            else:
                messages.append(
                    {"role": "user", "content": f"Unknown action '{action}'. Use 'use_tool' or 'complete'."}
                )

        except Exception as e:  # noqa: BLE001 — one subagent's crash is its own report
            logger.warning("Subagent [%s] step %s crashed: %s", subagent_id, step_num, e)
            findings_parts.append(f"[step {step_num}] crash: {e}")
            break

    findings, low_evidence = _compress_findings(findings_parts)
    if not findings:
        findings = "(no usable output produced)"
    elif low_evidence:
        findings = "(LOW EVIDENCE — verify independently)\n" + findings
    logger.info("Subagent [%s] done: success=%s steps=%s low_ev=%s", subagent_id, success, step_num, low_evidence)
    return SubagentResult(
        subagent_id=subagent_id, task=task, success=success, findings=findings, steps=step_num, partial=partial
    )


# ── Blocking batch API (compat; used by tests and sync callers) ───────────


async def spawn_and_collect(
    tasks: list[str],
    generate_fn,
    route_tool_fn,
    tool_catalog_fn=None,
    max_concurrent: int = 3,
    total_timeout: float | None = None,
) -> list[SubagentResult]:
    """Run subagents in parallel and WAIT for them (legacy blocking form)."""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run_one(task: str) -> SubagentResult:
        try:
            async with semaphore:
                return await run_subagent(
                    task, generate_fn=generate_fn, route_tool_fn=route_tool_fn, tool_catalog_fn=tool_catalog_fn
                )
        except Exception as e:  # noqa: BLE001
            logger.error("Subagent crash: %s — %s", task[:80], e)
            return SubagentResult(
                subagent_id="crash", task=task, success=False, findings=f"Subagent crashed: {e}", steps=0
            )

    results = await asyncio.wait_for(
        asyncio.gather(*[_run_one(t) for t in tasks], return_exceptions=True),
        timeout=total_timeout or (SUBAGENT_TIMEOUT + 30),
    )
    out = []
    for r in results:
        if isinstance(r, Exception):
            out.append(
                SubagentResult(
                    subagent_id="error", task="(unknown)", success=False, findings=f"Exception: {r}", steps=0
                )
            )
        else:
            out.append(r)
    return out


# ── Fireteam: non-blocking teams + result delivery on future turns ────────
#
# The main loop NEVER blocks on a team: deploy returns a team id instantly;
# background asyncio tasks run the specialists; each think turn drains
# finished results into the conversation as messages.
#
# State mirror: a JSON snapshot in the workspace so OTHER PROCESSES (the
# desktop gateway) can serve live fireteam status — the in-memory dict
# stays the source of truth inside the agent process.

_FIRETEAMS: dict[str, dict] = {}
_STATE_FILE_NAME = "fireteam.json"


def _state_path() -> Path:
    from suijin.modules.platform.lib.workspace import artifact_dir

    return artifact_dir("fireteam") / "registry.json"


def _persist_state() -> None:
    """Mirror the registry to outputs/fireteam/registry.json. Never raises."""
    try:
        import json as _json

        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps(_snapshot(), indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001 — mirroring must never break a team
        pass


def _snapshot() -> dict:
    """Serializable registry snapshot: teams with per-task live status."""
    teams = []
    for tid, t in sorted(_FIRETEAMS.items()):
        running = len(t["futures"])
        tasks = []
        results_by_task = {r.task: r for r in t["results"]}
        for task in t["tasks"]:
            r = results_by_task.get(task)
            if r is not None:
                tasks.append(
                    {
                        "task": task,
                        "state": "done",
                        "success": r.success,
                        "steps": r.steps,
                        "findings": r.findings[:1000],
                    }
                )
            elif running:
                tasks.append({"task": task, "state": "running", "success": None, "steps": None, "findings": ""})
            else:
                tasks.append({"task": task, "state": "queued", "success": None, "steps": None, "findings": ""})
        teams.append({"team_id": tid, "started": t["started"], "running": running, "tasks": tasks})
    return {"teams": teams, "updated": datetime.now(timezone.utc).isoformat()}


def deploy_fireteam(
    tasks: list[str],
    *,
    generate_fn,
    route_tool_fn,
    max_concurrent: int = 3,
) -> str:
    """Spawn specialists in the background; returns a team id immediately.

    MUST be called from inside the running event loop (think_node is).
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    # Display isolation: subagent streams never render — only the PRIMARY's
    # thought typewrites. Pass on_delta=False when the callable accepts it.
    import inspect as _inspect

    try:
        _params = _inspect.signature(generate_fn).parameters
        _sinkable = "on_delta" in _params or any(p.kind == p.VAR_KEYWORD for p in _params.values())
    except (TypeError, ValueError):
        _sinkable = False

    if _sinkable:
        _gen = generate_fn

        async def generate_fn(task_messages, config=None, **kw):  # noqa: F811 — deliberate shadow
            kw["on_delta"] = False
            return await _gen(task_messages, config, **kw)

    async def _run_one(task: str) -> SubagentResult:
        try:
            async with semaphore:
                return await run_subagent(task, generate_fn=generate_fn, route_tool_fn=route_tool_fn)
        except Exception as e:  # noqa: BLE001
            return SubagentResult(
                subagent_id="crash", task=task, success=False, findings=f"Subagent crashed: {e}", steps=0
            )

    spawned: list[str] = []
    skipped: list[tuple[str, str]] = []
    for t in tasks:
        reason = _gate_task(t)
        if reason is None:
            spawned.append(t)
            _RECENT_TASKS[_norm_task(t)] = time.monotonic()
        else:
            skipped.append((t, reason))
    team_id = f"team-{uuid.uuid4().hex[:6]}"

    if not spawned:
        # nothing worth a specialist — tell the main agent WHY, in full
        return {
            "team_id": None,
            "spawned": [],
            "skipped": skipped,
            "note": "no specialists deployed — every task was rejected; fix the tasks or do them yourself with use_tool",
        }
    futures = [asyncio.get_running_loop().create_task(_run_one(t)) for t in spawned]
    _FIRETEAMS[team_id] = {
        "tasks": spawned,
        "futures": futures,
        "started": datetime.now(timezone.utc).isoformat(),
        "results": [],
    }
    _persist_state()
    logger.info("Fireteam %s deployed: %d specialist(s), %d rejected", team_id, len(spawned), len(skipped))
    return {"team_id": team_id, "spawned": spawned, "skipped": skipped}


def collect_finished_teams(max_messages: int = 6) -> list[str]:
    """Drain completed fireteam results as conversation messages.

    Called at the START of every think turn: finished specialists'
    findings arrive as user messages on future turns, exactly like
    background job output.
    """
    messages: list[str] = []
    for team_id in list(_FIRETEAMS):
        team = _FIRETEAMS[team_id]
        for fut in list(team["futures"]):
            if not fut.done():
                continue
            team["futures"].remove(fut)
            try:
                r: SubagentResult = fut.result()
            except Exception as e:  # noqa: BLE001
                r = SubagentResult(subagent_id="error", task="?", success=False, findings=f"task crashed: {e}", steps=0)
            team["results"].append(r)
            if r.success:
                status = "OK"
            elif r.partial:
                status = "TIMEOUT (partial findings)"
            else:
                status = "FAILED"
            messages.append(
                f"FIRETEAM RESULT [{team_id}] {status} ({r.steps} steps)\n"
                f"Task: {r.task[:200]}\nFindings:\n{r.findings[:2000]}"
            )
            if len(messages) >= max_messages:
                _persist_state()
                return messages
        if not team["futures"] and team_id in _FIRETEAMS:
            del _FIRETEAMS[team_id]  # fully drained — forget it
    _persist_state()
    return messages


_LAST_STATUS_SIG: tuple | None = None


def fireteam_status() -> str:
    """Snapshot for the fireteam_status tool: running + recently finished.

    Nudges the agent back to work when nothing changed since its last
    check (results drain automatically — polling is near-pointless)."""
    global _LAST_STATUS_SIG
    sig = tuple((tid, len(t["futures"]), len(t["results"])) for tid, t in sorted(_FIRETEAMS.items()))
    nudge = ""
    if sig == _LAST_STATUS_SIG and sig:
        nudge = "No change since your last check — results arrive automatically; keep working.\n\n"
    _LAST_STATUS_SIG = sig
    if not _FIRETEAMS:
        return "No fireteams running. Deploy with action=deploy_subagent (tasks separated by ||)."
    lines = []
    for team_id, team in _FIRETEAMS.items():
        running = len(team["futures"])
        done = len(team["results"])
        lines.append(f"{team_id}: {running} running, {done} finished")
        results_by_task = {r.task: r for r in team["results"]}
        for t in team["tasks"]:
            r = results_by_task.get(t)
            if r is not None:
                mark = "done OK" if r.success else ("done TIMEOUT" if r.partial else "done FAILED")
            elif running:
                mark = "running"
            else:
                mark = "queued"
            lines.append(f"  - [{mark}] {t[:90]}")
    return nudge + "\n".join(lines)


def _reset_fireteams() -> None:
    """Test hook: cancel and forget everything."""
    for team in _FIRETEAMS.values():
        for fut in team["futures"]:
            fut.cancel()
    _FIRETEAMS.clear()
    _persist_state()
    _RECENT_TASKS.clear()
    global _LAST_STATUS_SIG
    _LAST_STATUS_SIG = None
