"""The module-global lint — no NEW hidden state machines (2026-10-30).

The structural disease behind the field bugs: module-level mutable
globals (_THIS, _THAT) each carry a private state machine with private
guards; every guard is another place to be wrong. The count at freeze
time: 137 (many are legitimate constant tables — pattern lists, maps —
which is why they are FROZEN, not banned).

THE RULE: state that must survive a turn belongs in the graph state
store (registered key, one owner). A new module-level mutable in the
agent/tools/redteam trees fails this test — move it to the state store
or make it a frozen constant (tuple, not list).
"""

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TREES = ["suijin/server/agent", "suijin/server/tools", "suijin/server/redteam"]


def _module_mutables(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    out = []
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            ann = ast.unparse(node.annotation) if node.annotation else ""
            if any(m in ann for m in ("list", "dict", "set", "List", "Dict", "Set")):
                out.append(node.target.id)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    v = node.value
                    if isinstance(v, (ast.List, ast.Dict, ast.Set)) or (
                        isinstance(v, ast.Call) and getattr(v.func, "id", "") in ("list", "dict", "set", "defaultdict")
                    ):
                        out.append(t.id)
    return out


def _current() -> set[str]:
    found = set()
    for tree in TREES:
        for p in (REPO / tree).rglob("*.py"):
            if p.name.startswith("._"):
                continue
            for name in _module_mutables(p):
                found.add(f"{p.relative_to(REPO).as_posix()}:{name}")
    return found


# frozen at the 2026-10-30 boundary — SHRINK it when you can, never grow it
BASELINE = {
    "suijin/server/agent/lib/cheatsheet.py:_cache",
    "suijin/server/agent/lib/contamination_scrub.py:_CRED_PATTERNS",
    "suijin/server/agent/lib/contamination_scrub.py:_DEAD_MARKERS",
    "suijin/server/agent/lib/context_distill.py:_SECRET_RES",
    "suijin/server/agent/lib/drive_bandit.py:DEFAULT_POLICY",
    "suijin/server/agent/lib/engagement.py:DEFAULT_SCHEMA",
    "suijin/server/agent/lib/epistemic.py:_STOPWORDS",
    "suijin/server/agent/lib/epistemic.py:_SURPRISE_PATTERNS",
    "suijin/server/agent/lib/landscape.py:_GENERIC_FAMILIES",
    "suijin/server/agent/lib/landscape.py:_KNOWN_FAMILIES",
    "suijin/server/agent/lib/librarian.py:_PATTERNS",
    "suijin/server/agent/lib/librarian.py:_UI_PUBLISH_COUNT",
    "suijin/server/agent/lib/loop_detection.py:_LAST_OUTPUTS",
    "suijin/server/agent/lib/mesh.py:_BAD_HELLO_PIDS",
    "suijin/server/agent/lib/mesh.py:_node",
    "suijin/server/agent/lib/mesh.py:_peers",
    "suijin/server/agent/lib/mesh.py:_tail_state",
    "suijin/server/agent/lib/mode_governor.py:_SKILL_HINTS",
    "suijin/server/agent/lib/mode_governor.py:_SURFACE_PATTERNS",
    "suijin/server/agent/lib/nodes/subagent_node.py:_FIRETEAMS",
    "suijin/server/agent/lib/nodes/subagent_node.py:_RECENT_TASKS",
    "suijin/server/agent/lib/nodes/subagent_node.py:_SCOPE_KEYWORDS",
    "suijin/server/agent/lib/nodes/subagent_node.py:_TEAM_ARCHIVE",
    "suijin/server/agent/lib/observation/__init__.py:_OBSERVATIONS",
    "suijin/server/agent/lib/profiles.py:DEFAULT_RULES",
    "suijin/server/agent/lib/profiles.py:DEFAULT_SOULS",
    "suijin/server/agent/lib/profiles.py:PROFILES",
    "suijin/server/agent/lib/prompts/tool_registry.py:PHASE_TOOLS",
    "suijin/server/agent/lib/prompts/tool_registry.py:TOOL_REGISTRY",
    "suijin/server/agent/lib/prompts/tool_registry.py:_ALL_TOOLS",
    "suijin/server/agent/lib/reflex/__init__.py:__all__",
    "suijin/server/agent/lib/reflex/chain_composer.py:_PENDING_HANDOFF",
    "suijin/server/agent/lib/reflex/core/client.py:DEFAULTS",
    "suijin/server/agent/lib/reflex/core/client.py:STATS",
    "suijin/server/agent/lib/reflex/core/client.py:_LAYA_AGENT",
    "suijin/server/agent/lib/reflex/core/features.py:RECON_TOOLS",
    "suijin/server/agent/lib/reflex/core/questions.py:_QUESTIONS",
    "suijin/server/agent/lib/reflex/drifter/__init__.py:__all__",
    "suijin/server/agent/lib/reflex/oracle/__init__.py:__all__",
    "suijin/server/agent/lib/reflex/supervisor/__init__.py:__all__",
    "suijin/server/agent/lib/reflex/supervisor/verdict.py:_GROUNDS",
    "suijin/server/agent/lib/skills/loader.py:SKILL_MAP",
    "suijin/server/agent/lib/state.py:GRAPH_LABELS",
    "suijin/server/agent/lib/state.py:GRAPH_RELATIONSHIPS",
    "suijin/server/agent/lib/state.py:_PRIORITY_SYNONYMS",
    "suijin/server/agent/lib/supervisor.py:PARALLEL_LIMITS",
    "suijin/server/agent/lib/supervisor.py:PHASE_TRANSITIONS",
    "suijin/server/agent/lib/supervisor.py:RETRY_POLICY",
    "suijin/server/agent/lib/supervisor.py:TACTICAL_FOLLOWUPS",
    "suijin/server/agent/lib/supervisor.py:_INJECTION_TOOLS",
    "suijin/server/agent/lib/supervisor.py:_INTERVENTION_STATE",
    "suijin/server/agent/lib/supervisor.py:_PAYLOAD_FAMILIES",
    "suijin/server/agent/lib/supervisor.py:_TACTICAL_COOLDOWN",
    "suijin/server/agent/lib/supervisor.py:_last_fired",
    "suijin/server/agent/lib/target_board.py:_CRED_RES",
    "suijin/server/agent/lib/verify.py:_RECIPES",
    "suijin/server/agent/lib/verify.py:_RECIPE_ALIASES",
    "suijin/server/agent/lib/vuln_agents/__init__.py:PROFILES",
    "suijin/server/agent/lib/weaponization.py:CLASS_WEIGHTS",
    "suijin/server/agent/lib/weaponization.py:_SCENARIO_PATTERNS",
    "suijin/server/agent/lib/weaponization.py:_SEVERITY_WEIGHTS",
    "suijin/server/agent/lib/weaponizer.py:PLAYBOOKS",
    "suijin/server/agent/lib/weaponizer.py:_DEFAULT_PLAYBOOK",
    "suijin/server/redteam/lib/intel/drift_analyser.py:_PATTERN_LABELS",
    "suijin/server/redteam/lib/intel/kg_backend.py:_BACKEND_CACHE",
    "suijin/server/redteam/lib/intel/kg_backend.py:_ENV_CACHE",
    "suijin/server/redteam/lib/intel/oracle.py:_SEVERITY_RANK",
    "suijin/server/redteam/lib/intel/oracle.py:_SIGNAL_CVSS",
    "suijin/server/redteam/lib/redteamer.py:_LIVE_AGENT_STATE",
    "suijin/server/tools/lib/ai_env.py:_TOOL_CATEGORIES",
    "suijin/server/tools/lib/attack_scripts.py:_LISTENERS",
    "suijin/server/tools/lib/audit_trail.py:_last_flush",
    "suijin/server/tools/lib/audit_trail.py:_locks",
    "suijin/server/tools/lib/audit_trail.py:_trails",
    "suijin/server/tools/lib/availability.py:_IMPORT_ALIASES",
    "suijin/server/tools/lib/availability.py:_INSTALL",
    "suijin/server/tools/lib/availability.py:_WHICH_ALIASES",
    "suijin/server/tools/lib/bbp_report.py:_CWE_MAP",
    "suijin/server/tools/lib/bbp_report.py:_IMPACT_MAP",
    "suijin/server/tools/lib/bbp_report.py:_REMEDIATION_MAP",
    "suijin/server/tools/lib/code_harness.py:_LANG_DEFAULT_RUN",
    "suijin/server/tools/lib/coverage.py:_NOTES",
    "suijin/server/tools/lib/dispatch.py:_FALLBACKS",
    "suijin/server/tools/lib/dispatch.py:_FETCHED_AUTH_TARGETS",
    "suijin/server/tools/lib/dispatch.py:_REPEAT_STATE",
    "suijin/server/tools/lib/dispatch.py:_TOOL_ALTERNATIVES",
    "suijin/server/tools/lib/dispatch.py:_TOOL_DESC_CACHE",
    "suijin/server/tools/lib/dispatch.py:__all__",
    "suijin/server/tools/lib/ensure_tools.py:_TOOLCHAIN",
    "suijin/server/tools/lib/exploit_catalog.py:_DIFFERENTIAL_CLASSES",
    "suijin/server/tools/lib/exploit_catalog.py:_ECHO_COMMANDS",
    "suijin/server/tools/lib/exploit_catalog.py:_SEVERITY_LADDER",
    "suijin/server/tools/lib/guardrails.py:_BLOCKED_PATTERNS",
    "suijin/server/tools/lib/guardrails.py:_OPERATOR_OVERRIDE",
    "suijin/server/tools/lib/http_replay.py:_BUDGET",
    "suijin/server/tools/lib/http_replay.py:_CREDENTIALS",
    "suijin/server/tools/lib/http_replay.py:_ERROR_SIGS",
    "suijin/server/tools/lib/http_replay.py:_LAST_SEND",
    "suijin/server/tools/lib/http_replay.py:_LIMITER",
    "suijin/server/tools/lib/inject_probe.py:_CMD_FORMS",
    "suijin/server/tools/lib/inject_probe.py:_LFI_SHAPES",
    "suijin/server/tools/lib/inject_probe.py:_LFI_TARGETS",
    "suijin/server/tools/lib/inject_probe.py:_SQLI_BOOL_PAIRS",
    "suijin/server/tools/lib/inject_probe.py:_SQLI_ERRORS",
    "suijin/server/tools/lib/inject_probe.py:_SSTI_SYNTAXES",
    "suijin/server/tools/lib/inject_probe.py:_XSS_TAGS",
    "suijin/server/tools/lib/inject_probe.py:_XSS_WEAPONS",
    "suijin/server/tools/lib/job_registry.py:_drained",
    "suijin/server/tools/lib/job_registry.py:_jobs",
    "suijin/server/tools/lib/js_tools.py:SECRET_RES",
    "suijin/server/tools/lib/modes.py:_GUARDRAIL_BLOCKED_BINARIES",
    "suijin/server/tools/lib/modes.py:_HITL_ALLOWED_BINARIES",
    "suijin/server/tools/lib/modes.py:_HITL_ALLOWED_TOOLS",
    "suijin/server/tools/lib/module_sdk.py:_MANIFEST_TEMPLATE",
    "suijin/server/tools/lib/payload_generator.py:PAYLOAD_DB",
    "suijin/server/tools/lib/rate_limit_detector.py:_tracker",
    "suijin/server/tools/lib/recipes.py:BUILT_IN_RECIPES",
    "suijin/server/tools/lib/report_exporter.py:_REMEDIATION",
    "suijin/server/tools/lib/report_templates.py:_LANG",
    "suijin/server/tools/lib/self_config.py:ALLOWLIST",
    "suijin/server/tools/lib/self_config.py:_HOLDER",
    "suijin/server/tools/lib/services.py:_cache",
    "suijin/server/tools/lib/services.py:_producers",
    "suijin/server/tools/lib/session_aware.py:USER_AGENTS",
    "suijin/server/tools/lib/session_aware.py:_sessions",
    "suijin/server/tools/lib/shell_session.py:_SESSIONS",
    "suijin/server/tools/lib/sjpack.py:KIND_BY_EXT",
    "suijin/server/tools/lib/skill_library.py:_INDEX",
    "suijin/server/tools/lib/surface_expand.py:_DERIVATIONS",
    "suijin/server/tools/lib/surface_expand.py:_WORDLIST",
    "suijin/server/tools/lib/tester_fleet.py:TESTER_DOCTRINES",
    "suijin/server/tools/lib/tester_fleet.py:_LANE_COVERAGE",
    "suijin/server/tools/lib/tool_contracts/__init__.py:_cache",
    "suijin/server/tools/lib/tool_manifest.py:RUNTIME_PACKAGES",
    "suijin/server/tools/lib/tool_manifest.py:_MANIFEST_CACHE",
    "suijin/server/tools/lib/tool_manifest.py:_OVERRIDES",
    "suijin/server/tools/lib/tool_manifest.py:_PLATFORM_KEYS",
    "suijin/server/tools/lib/tool_manifest.py:_PY_MODULES",
    "suijin/server/tools/lib/url_hygiene.py:_ID_PARAM_NAMES",
    "suijin/server/tools/lib/ux_tools.py:_action_cache",
    "suijin/server/tools/lib/web_session.py:_LAST",
    "suijin/server/tools/lib/web_session.py:_UI_FIELDS",
}


class TestNoNewModuleGlobals:
    def test_no_new_module_level_mutables(self):
        current = _current()
        new = current - BASELINE
        assert not new, (
            "NEW module-level mutable globals (hidden state machines — the "
            "structural disease behind the ask-loop bug class). Move the state "
            "into the graph state store (registered key) or make it a frozen "
            "constant (tuple):\n  " + "\n  ".join(sorted(new))
        )

    def test_baseline_entries_still_exist(self):
        """Deleted entries leave stale baseline rows — prune them so the
        baseline only ever shrinks."""
        current = _current()
        stale = BASELINE - current
        assert not stale, f"stale baseline rows (prune them): {sorted(stale)[:5]}…"
