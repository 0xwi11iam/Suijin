"""Suijin tool dispatcher.

This module is the public routing surface for every tool. The implementations
live in focused sibling modules; this file assembles the route table and
re-exports the public API for backwards compatibility.

Layout:
  runtime.py      shared state (session, proxy, paths, helpers)
  terminal.py     execute_terminal
  http_tools.py   http_request, apply_patch, read_file, write_file
  metasploit.py   msf_* integration
  intel.py        search_cve, search_kb, knowledge graph, write_note
  kb_tools.py     find_wordlist, kb_stats, suggest_exploit, extract_payloads,
                  wordlist_tool, mine_failures, anonymize_report
  reporting.py    payload/diff/rate-limit/attack-tree/report wrappers
  jobs.py         background job management
  aux_tools.py    web search, self-improvement, pip install
"""

from __future__ import annotations

# Cross-module re-export surface: delegated lazily (boundary rule — no
# module-level cross-module imports; delegation is also patch-transparent
# for consumers importing these names from dispatch).
_KB_TOOLS_NAMES = (
    "anonymize_report",
    "extract_payloads",
    "find_wordlist",
    "kb_stats",
    "mine_failures",
    "suggest_exploit",
    "wordlist_tool",
)
_RUNTIME_NAMES = (
    "BASE_DIR",
    "DB_PATH",
    "PROJECT_DIR",
    "_recon_state",
    "get_proxy",
    "global_session",
    "reset_recon_state",
    "set_proxy",
    "truncate",
)


def get_module_tools():
    """Loader module tools (patchable seam — tests stub this on dispatch)."""
    from suijin.modules.loader import get_module_tools as _gmt

    return _gmt()


_MISSING_RX = __import__("re").compile(r"not (?:installed|found)|command not found|No such file", __import__("re").I)


def _with_install_hint(tool_name: str, result: str) -> str:
    """The catalog's kept promise: a missing-binary error gets THIS
    operator's install command appended — the agent self-serves instead
    of stalling. Best-effort, never changes success results."""
    try:
        text = str(result)
        if not text.startswith(("Error", "Tool Error", "Tool error")) or not _MISSING_RX.search(text):
            return result
        from suijin.modules.tools.lib.availability import install_hint, tool_dependencies

        deps = tool_dependencies().get(tool_name) or []
        hints = [f"→ install: {install_hint(b)}" for b in deps if install_hint(b)]
        if not hints:
            return result
        return text + "\n" + "\n".join(hints[:2]) + "\n(you may install it via execute_terminal, then retry)"
    except Exception:  # noqa: BLE001 — hints must never break a result
        return result


def __getattr__(name):
    if name in _KB_TOOLS_NAMES:
        from suijin.modules.knowledge.lib import kb_tools

        return getattr(kb_tools, name)
    if name in ("build_dossier", "render_dossier"):
        from suijin.modules.ops.lib import dossier

        return getattr(dossier, name)
    if name in _RUNTIME_NAMES:
        from suijin.modules.platform.lib import runtime

        return getattr(runtime, name)
    if name in ("WORKSPACE_DIR", "resolve_workspace_path"):
        from suijin.modules.platform.lib import workspace

        return getattr(workspace, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Late-bound canonical modules: route lambdas resolve attributes at CALL
# time so patching the owning module (the test contract) affects routed
# calls. The from-imports below remain as the back-compat re-export surface.
from suijin.modules.tools.lib import intel as _intel
from suijin.modules.tools.lib import wordlist_mutator as _wordlist
from suijin.modules.tools.lib.aux_tools import (
    _edit_skill,
    _list_own_files,
    _list_skills,
    _pip_install,
    _web_search,
    _write_tool,
)
from suijin.modules.tools.lib.bypass_403 import bypass_403 as _bypass_403
from suijin.modules.tools.lib.code_harness import code_harness as _code_harness
from suijin.modules.tools.lib.exploit_catalog import catalog_exploit

# Re-exported for backwards compatibility — these names lived on dispatch.py
# before the split and external callers still import them from here.
# Everything listed in __all__ below is a deliberate re-export: ruff must
# not prune these "unused" imports.
from suijin.modules.tools.lib.guardrails import _BLOCKED_PATTERNS, confirm_global_action, is_dangerous
from suijin.modules.tools.lib.http_replay import (
    http_replay as _http_replay,
)
from suijin.modules.tools.lib.http_replay import (
    http_replay_raw as _http_replay_raw,
)
from suijin.modules.tools.lib.http_replay import (
    list_credentials as _list_credentials,
)
from suijin.modules.tools.lib.http_replay import (
    register_credential as _register_credential,
)
from suijin.modules.tools.lib.http_tools import apply_patch, http_request, read_file, write_file
from suijin.modules.tools.lib.inject_probe import inject_probe as _inject_probe
from suijin.modules.tools.lib.intel import (
    NOTES_DIR,
    NVD_BASE,
    _extract_cvss,
    _is_kev,
    check_knowledge,
    record_finding,
    search_cve,
    search_kb,
    write_note,
)
from suijin.modules.tools.lib.jobs import (
    _job_cancel,
    _job_list,
    _job_output,
    _job_status,
    _job_wait,
)
from suijin.modules.tools.lib.js_tools import google_key_probe, js_bundle_analyze, source_map_probe
from suijin.modules.tools.lib.output_normalizer import normalize_output
from suijin.modules.tools.lib.payload_mutate import payload_mutate as _payload_mutate
from suijin.modules.tools.lib.self_config import adjust_config as _adjust_config
from suijin.modules.tools.lib.web_session import web_session as _web_session


def _kb_read_tool(path: str) -> str:
    from suijin.modules.knowledge.lib.kb import read_doc

    try:
        source, rel, content = read_doc(path)
    except (FileNotFoundError, ValueError) as e:
        return f"Error: {e}"
    from suijin.modules.platform.lib.runtime import truncate

    return f"--- [{source}] {rel} ({len(content):,} chars)\n" + truncate(content, 20000)


def _target_dossier_tool(target: str) -> str:
    try:
        from suijin.modules.ops.lib.dossier import build_dossier, render_dossier

        return render_dossier(build_dossier(target))
    except ValueError as e:
        return f"Error: {e}"


# ── Re-export the public tool surface ─────────────────────────────────
from suijin.modules.tools.lib.job_registry import _job_lock, _jobs  # noqa: F401 — re-exports (THE registry)
from suijin.modules.tools.lib.metasploit import (
    _msf_console_fallback,
    _msf_rpc_connect,
    msf_check,
    msf_command,
    msf_run,
    msf_sessions,
)
from suijin.modules.tools.lib.modes import check_mode_restrictions
from suijin.modules.tools.lib.reporting import (
    _attack_tree,
    _diff_resp,
    _gen_report,
    _payload_gen,
    _rate_all,
    _rate_check,
)
from suijin.modules.tools.lib.terminal import execute_terminal

__all__ = [
    # guardrails
    "_BLOCKED_PATTERNS",
    "confirm_global_action",
    "is_dangerous",
    # workspace
    "WORKSPACE_DIR",  # noqa: F822 — via module __getattr__ delegation
    "resolve_workspace_path",  # noqa: F822 — via module __getattr__ delegation
    # runtime
    "BASE_DIR",  # noqa: F822 — via module __getattr__ delegation
    "DB_PATH",  # noqa: F822 — via module __getattr__ delegation
    "PROJECT_DIR",  # noqa: F822 — via module __getattr__ delegation
    "_job_lock",
    "_jobs",
    "_recon_state",  # noqa: F822 — via module __getattr__ delegation
    "get_proxy",  # noqa: F822 — via module __getattr__ delegation
    "global_session",  # noqa: F822 — via module __getattr__ delegation
    "reset_recon_state",  # noqa: F822 — via module __getattr__ delegation
    "set_proxy",  # noqa: F822 — via module __getattr__ delegation
    "truncate",  # noqa: F822 — via module __getattr__ delegation
    # terminal / http
    "execute_terminal",
    "apply_patch",
    "http_request",
    "read_file",
    "write_file",
    # metasploit
    "_msf_console_fallback",
    "_msf_rpc_connect",
    "msf_check",
    "msf_command",
    "msf_run",
    "msf_sessions",
    # intel
    "NVD_BASE",
    "NOTES_DIR",
    "_extract_cvss",
    "_is_kev",
    "check_knowledge",
    "record_finding",
    "search_cve",
    "search_kb",
    "write_note",
    # kb toolkit
    "anonymize_report",  # noqa: F822 — via module __getattr__ delegation
    "extract_payloads",  # noqa: F822 — via module __getattr__ delegation
    "find_wordlist",  # noqa: F822 — via module __getattr__ delegation
    "kb_stats",  # noqa: F822 — via module __getattr__ delegation
    "mine_failures",  # noqa: F822 — via module __getattr__ delegation
    "suggest_exploit",  # noqa: F822 — via module __getattr__ delegation
    "wordlist_tool",  # noqa: F822 — via module __getattr__ delegation
    # jobs
    "_job_cancel",
    "_job_list",
    "_job_output",
    "_job_status",
    "_job_wait",
    # reporting
    "_attack_tree",
    "_diff_resp",
    "_gen_report",
    "_payload_gen",
    "_rate_all",
    "_rate_check",
    # aux
    "_edit_skill",
    "_list_own_files",
    "_list_skills",
    "_pip_install",
    "_web_search",
    "_write_tool",
    # routing
    "route_tool",
    "get_tool_catalog",
    "list_route_tools",
]


def _recon_chain_route(target, config, ports=None):
    from suijin.modules.tools.lib.recon import recon_chain

    return recon_chain(target, config=config, ports=ports)


def _build_routes(config):
    from suijin.modules.knowledge.lib.kb_tools import (
        anonymize_report,
        extract_payloads,
        find_wordlist,
        kb_stats,
        mine_failures,
        suggest_exploit,
        wordlist_tool,
    )

    routes = {
        "execute_terminal": lambda a: execute_terminal(
            a.get("cmd") or a.get("command"), timeout=int(a.get("timeout", 30))
        ),
        "search_kb": lambda a: _intel.search_kb(a.get("keyword"), limit=a.get("limit") or 5),
        # SPA attack-surface mining (one call instead of hand-rolled curl+grep)
        "js_bundle_analyze": lambda a: js_bundle_analyze(a.get("url", "")),
        "fetch_authorization_page": lambda a: _fetch_auth_page(a.get("target", ""), a.get("url", "")),
        "google_key_probe": lambda a: google_key_probe(a.get("key", "")),
        "source_map_probe": lambda a: source_map_probe(a.get("url", "")),
        "kb_read": lambda a: _kb_read_tool(a.get("path", "")),
        "normalize_output": lambda a: normalize_output(a.get("output", ""), kind=a.get("kind", "auto")),
        "target_dossier": lambda a: _target_dossier_tool(a.get("target", "")),
        "mutate_wordlist": lambda a: _wordlist.mutate_wordlist(
            a.get("seeds"),
            out=a.get("out", "wordlists/mutated.txt"),
            leet=bool(a.get("leet", True)),
            years=bool(a.get("years", True)),
            suffixes=bool(a.get("suffixes", True)),
        ),
        "cewl_words": lambda a: _wordlist.cewl_words(
            a.get("url", ""), out=a.get("out"), min_len=int(a.get("min_len", 3)), max_len=int(a.get("max_len", 24))
        ),
        # Knowledge-base toolkit (offline)
        "kb_stats": lambda a: kb_stats(),
        "find_wordlist": lambda a: find_wordlist(a.get("keyword"), extract=a.get("extract", True)),
        "suggest_exploit": lambda a: suggest_exploit(a.get("service"), a.get("version", "")),
        "extract_payloads": lambda a: extract_payloads(a.get("keyword"), max_payloads=int(a.get("max_payloads", 10))),
        "wordlist_tool": lambda a: wordlist_tool(
            a.get("action"),
            a.get("files"),
            out=a.get("out", ""),
            min_len=int(a.get("min_len", 1)),
            max_len=int(a.get("max_len", 256)),
        ),
        "mine_failures": lambda a: mine_failures(max_clusters=int(a.get("max_clusters", 5))),
        "anonymize_report": lambda a: anonymize_report(a.get("file_path", "")),
        "http_request": lambda a: http_request(a.get("method", "GET"), a.get("url"), a.get("headers"), a.get("body")),
        "bypass_403": lambda a: _bypass_403(a.get("url", "")),
        "http_replay": lambda a: _http_replay(
            request_id=a.get("request_id", ""),
            method=a.get("method", "GET"),
            url=a.get("url", ""),
            headers=a.get("headers"),
            body=a.get("body", ""),
            mutations=a.get("mutations"),
            codec=a.get("codec"),
            codec_field=a.get("codec_field", ""),
            credential=a.get("credential", ""),
            unauthenticated=bool(a.get("unauthenticated")),
            compare=a.get("compare"),
            sweep=a.get("sweep"),
            follow_redirects=bool(a.get("follow_redirects")),
            timeout=int(a.get("timeout", 30)),
            allow_internal=bool(a.get("allow_internal")),
        ),
        "http_replay_raw": lambda a: _http_replay_raw(
            host=a.get("host", ""), port=int(a.get("port", 443)), tls=bool(a.get("tls", True)),
            data=a.get("data", ""), timeout=int(a.get("timeout", 15)),
        ),
        "register_credential": lambda a: _register_credential(
            name=a.get("name", ""), headers=a.get("headers"), cookies=a.get("cookies", "")
        ),
        "list_credentials": lambda a: _list_credentials(),
        "web_session": lambda a: _web_session(action=a.get("action", "summary")),
        "inject_probe": lambda a: _inject_probe(
            url=a.get("url", ""),
            method=a.get("method", "GET"),
            headers=a.get("headers"),
            body=a.get("body", ""),
            vuln_class=a.get("vuln_class", "xss"),
            field=a.get("field", "q"),
            in_body=bool(a.get("in_body")),
            request_id=a.get("request_id", ""),
            timeout=int(a.get("timeout", 20)),
            allow_internal=bool(a.get("allow_internal")),
        ),
        "adjust_config": lambda a: _adjust_config(**(a or {})),
        "payload_mutate": lambda a: _payload_mutate(
            a.get("payload", ""), blocked_response=a.get("blocked_response", ""), vuln_class=a.get("vuln_class", "")
        ),
        "code_harness": lambda a: _code_harness(
            a.get("goal", ""),
            language=a.get("language", "python"),
            code=a.get("code", ""),
            run_cmd=a.get("run_cmd", ""),
            success_regex=a.get("success_regex", ""),
            fail_regex=a.get("fail_regex", ""),
            filename=a.get("filename", ""),
            timeout_s=int(a.get("timeout_s", 30)),
            max_cycles=int(a.get("max_cycles", 3)),
        ),
        "read_file": lambda a: read_file(a.get("file_path", "")),
        "write_file": lambda a: write_file(a.get("file_path", ""), a.get("content", "")),
        "apply_patch": lambda a: apply_patch(a.get("vulnerability"), a.get("file_path", "lab.py")),
        "claim_flag": lambda a: f"OBJECTIVE MET: {a.get('flag')}",
        # Recon orchestration
        "recon_chain": lambda a: _recon_chain_route(a.get("target"), config, a.get("ports")),
        # Metasploit tools
        "msf_check": lambda a: msf_check(config),
        "msf_command": lambda a: msf_command(a.get("cmd") or a.get("command"), config),
        "msf_run": lambda a: msf_run(a.get("module"), a.get("payload"), a.get("options") or {}, config),
        "msf_sessions": lambda a: msf_sessions(a.get("action", "list"), a.get("id"), config),
        # CVE / vulnerability intelligence
        "search_cve": lambda a: search_cve(
            a.get("software"), config, version=a.get("version"), limit=int(a.get("limit", 5))
        ),
        # Oracle / knowledge graph
        "check_knowledge": lambda a: check_knowledge(a.get("target"), payload=a.get("payload"), config=config),
        "record_finding": lambda a: record_finding(
            a.get("target"), a.get("finding_type"), a.get("rule"), evidence=a.get("evidence", ""), config=config
        ),
        # POC-backed exploit catalog — the blocking gate (runs the step-POC
        # before the agent can continue; verdict: CONFIRMED/FAILED_*)
        "catalog_exploit": lambda a: catalog_exploit(
            a.get("engagement"),
            a.get("target"),
            a.get("vuln_class") or a.get("class"),
            a.get("title", ""),
            poc=a.get("poc"),
            marker=a.get("marker", ""),
            guards=a.get("guards", ""),
            severity=a.get("severity", ""),
            cvss=a.get("cvss"),
            abandon=bool(a.get("abandon")),
            claim=bool(a.get("claim")),
            entry_id=a.get("entry_id", ""),
            config=config,
        ),
        # Note-taking
        "write_note": lambda a: write_note(
            a.get("content", ""),
            success=a.get("success", True),
            category=a.get("category", "general"),
            engagement=a.get("engagement"),
            config=config,
        ),
        # Web search & self-improvement
        "web_search": lambda a: _web_search(a.get("query", ""), int(a.get("max_results", 5))),
        "edit_skill": lambda a: _edit_skill(a.get("skill_name", ""), a.get("new_content", "")),
        "write_tool": lambda a: _write_tool(a.get("tool_name", ""), a.get("code", "")),
        "list_skills": lambda a: _list_skills(),
        "list_own_files": lambda a: _list_own_files(),
        "pip_install": lambda a: _pip_install(a.get("package", "")),
        # Background job management
        "job_status": lambda a: _job_status(a.get("job_id", "")),
        "job_wait": lambda a: _job_wait(a.get("job_id", ""), a.get("timeout", 60)),
        "job_output": lambda a: _job_output(a.get("job_id", "")),
        "job_list": lambda a: _job_list(),
        "job_cancel": lambda a: _job_cancel(a.get("job_id", "")),
        # Analysis & reporting
        "payload_generate": lambda a: _payload_gen(a.get("vuln_type", ""), a.get("framework", "")),
        "diff_response": lambda a: _diff_resp(
            a.get("baseline", ""), a.get("injected", ""), a.get("sensitivity", "medium")
        ),
        "rate_limit_check": lambda a: _rate_check(a.get("endpoint", "")),
        "rate_limit_all": lambda a: _rate_all(),
        "attack_tree": lambda a: _attack_tree(a.get("trace_json", "")),
        "generate_report": lambda a: _gen_report(
            a.get("engagement", ""), a.get("trace_json", ""), a.get("findings_json", "")
        ),
        # deploy_subagent is an ACTION, not a tool. If the AI accidentally uses
        # it as a tool_name, show EXACTLY how to fix it so it self-corrects.
        "deploy_subagent": lambda a: (
            "WRONG FORMAT. deploy_subagent is an ACTION type, not a tool_name.\n"
            'You used: {"action": "use_tool", "tool_name": "deploy_subagent", ...}\n'
            'USE INSTEAD: {"action": "deploy_subagent", "subagent_task": "your task", "thought": "..."}\n'
            "Separate multiple tasks with || for parallel execution.\n"
            'Example: {"action": "deploy_subagent", "subagent_task": "SQLi test on /login || XSS test on /search", "thought": "parallel attacks"}'
        ),
    }
    # Inject module tools dynamically
    for t_name, t_func in get_module_tools().items():  # module attr — patchable seam
        routes[t_name] = lambda a, f=t_func: f(**a)

    # Addon tools (suijin/addons/*/main.py — zero-boilerplate drops)
    try:
        from suijin.modules.addons.entry import get_addon_tools

        for t_name, t_func in get_addon_tools().items():
            routes.setdefault(t_name, lambda a, f=t_func: f(**(a or {})))
    except Exception:  # noqa: BLE001 — addons never break route building
        pass

    # Knowledge extras (B12/B18) + evidence (B13/B14/B15)
    try:
        from suijin.modules.knowledge.lib import advisor as _advisor
        from suijin.modules.tools.lib import evidence as _ev

        routes["cve_advise_tools"] = lambda a: _advisor.advise_tools(a.get("keyword", ""))
        routes["kb_freshness"] = lambda a: _advisor.kb_freshness()
        routes["evidence_capture"] = lambda a: (
            "sealed " + (_ev.capture(a, a.get("evidence_text", "")) or {}).get("chain_hash", "")[:16]
        )
        routes["evidence_verify"] = lambda a: (
            "CHAIN OK" if _ev.verify_chain()[0] else "CHAIN BROKEN: " + "; ".join(_ev.verify_chain()[1][:3])
        )
        routes["finding_dedup"] = lambda a: (
            f"{len(_ev.dedup(a.get('findings', [])))} unique of {len(a.get('findings', []))}"
        )
        routes["attack_paths"] = lambda a: _ev.score_paths(a.get("findings", []))
    except Exception:  # noqa: BLE001
        pass

    # Fireteam status (v5.1): poll background specialist teams
    try:
        from suijin.modules.agent.lib.nodes.subagent_node import fireteam_status

        routes["fireteam_status"] = lambda a: fireteam_status()
    except Exception:  # noqa: BLE001
        pass

    # Recipes (A3): named multi-tool macros
    try:
        from suijin.modules.tools.lib import recipes as _recipes

        routes["recipe_run"] = lambda a: _recipes.recipe_run(a.get("name", ""), a.get("target", ""), route_tool)
        routes["recipe_list"] = lambda a: _recipes.recipe_list()
        routes["recipe_define"] = lambda a: _recipes.recipe_define(a.get("name", ""), a.get("steps_json", ""))
    except Exception:  # noqa: BLE001 — recipes never break route building
        pass

    return routes


def list_route_tools():
    """Return the names of every dispatchable tool (explicit + module tools)."""
    return sorted(_build_routes(None).keys())


# ── Self-healing execution (transient failures only) ──────────────────
# Network-shaped failures (timeouts, connection resets, DNS blips) get a
# short backoff retry — the same call, unchanged: these errors are
# environmental, not behavioral. Everything else (bad args, auth, logic)
# returns immediately so the agent can adjust on its next turn — blindly
# retrying a logical error would just burn the engagement clock.
_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "connectionerror",
    "connection reset",
    "temporarily unavailable",
    "rate limited by provider",
    "502",
    "503",
    "504",
)
_RETRY_BACKOFF_S = (1.0, 3.0)  # two retries max, then report


def _is_transient(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(m in text for m in _TRANSIENT_MARKERS)


def _execute_with_healing(fn, args: dict, tool_name: str):
    """Run a tool route with transient-failure retry. Never raises."""
    import time as _time

    attempts = 1 + len(_RETRY_BACKOFF_S)
    for i in range(attempts):
        try:
            return fn(args)
        except Exception as e:
            transient = _is_transient(e)
            if not transient or i == attempts - 1:
                label = "transient failure persisted" if transient else "error"
                return f"Tool Error ({tool_name}): {e} [{label}, attempt {i + 1}/{attempts}]"
            _time.sleep(_RETRY_BACKOFF_S[i])
    return f"Tool Error ({tool_name}): unreachable"


def _fetch_auth_page(target, url):
    from suijin.modules.ops.lib.authorizations import fetch_page

    return fetch_page(target, url)


# ── H3: dispatch-layer anti-repeat ─────────────────────────────────────
# Field trace: ONE identical failing http_request repeated 80 times across
# 9.5 hours. Prompts are suggestions; this is a law. Key = (tool, canonical
# args); 3 consecutive failures of the SAME call hard-block with the last
# error + alternatives. Different args (payload iteration) always allowed;
# any success clears the counter. Env kill switch: SUIJIN_REPEAT_GUARD=0.

_REPEAT_STATE = {"fails": {}, "last_error": {}, "blocked": 0}
_REPEAT_LIMIT = 3
_FAILURE_PREFIXES = ("Error:", "Tool Error", "Tool error", "HTTP Error:", "Execution Fault:")

_TOOL_ALTERNATIVES = {
    "http_request": ("execute_terminal (curl with flags)", "mcp_browser_goto (JS-heavy pages)"),
    "bypass_403": ("http_request (manual variant crafting)",),
    "execute_terminal": ("http_request (raw HTTP)", "recon_chain (chained recon)"),
    "nmap": ("execute_terminal (nmap direct, background it)", "tcp_scan (port sweep)"),
    "search_kb": ("web_search", "search_cve"),
    "search_cve": ("search_kb (offline technique docs)", "web_search"),
}


def _repeat_key(tool_name, args) -> str:
    import hashlib
    import json as _json

    try:
        canon = _json.dumps(args or {}, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 — unserializable args fall back to repr
        canon = repr(args)
    return hashlib.sha1(f"{tool_name}|{canon}".encode()).hexdigest()[:16]


def _repeat_guard_check(tool_name, args) -> str | None:
    import os

    if os.environ.get("SUIJIN_REPEAT_GUARD") == "0":
        return None
    key = _repeat_key(tool_name, args)
    fails = _REPEAT_STATE["fails"].get(key, 0)
    if fails < _REPEAT_LIMIT:
        return None
    alts = _TOOL_ALTERNATIVES.get(tool_name) or _two_alternatives(tool_name)
    return (
        f"BLOCKED: this EXACT call has failed {fails} times in a row "
        f"(last error: {_REPEAT_STATE['last_error'].get(key, '?')[:200]}). "
        "Repeating identical failing calls is a dead end — the guard will keep blocking it. "
        f"Change the arguments/approach, or use: {'; '.join(alts)}. "
        "If you believe this is a harness bug, ask_operator."
    )


def _two_alternatives(tool_name: str) -> tuple[str, str]:
    import difflib

    pool = ["http_request", "execute_terminal", "search_kb", "check_knowledge", "web_search", "job_list"]
    close = [t for t in difflib.get_close_matches(tool_name, pool, n=3, cutoff=0.3) if t != tool_name]
    rest = [t for t in pool if t != tool_name and t not in close]
    picks = (close + rest)[:2]
    return (picks[0] if picks else "ask_operator", picks[1] if len(picks) > 1 else "ask_operator")


def _repeat_guard_record(tool_name, args, result: str) -> None:
    import os

    if os.environ.get("SUIJIN_REPEAT_GUARD") == "0":
        return
    key = _repeat_key(tool_name, args)
    failed = str(result or "").startswith(_FAILURE_PREFIXES)
    if failed:
        _REPEAT_STATE["fails"][key] = _REPEAT_STATE["fails"].get(key, 0) + 1
        _REPEAT_STATE["last_error"][key] = str(result)[:300]
        if len(_REPEAT_STATE["fails"]) > 200:  # bound memory on long engagements
            _REPEAT_STATE["fails"].clear()
            _REPEAT_STATE["last_error"].clear()
    else:
        _REPEAT_STATE["fails"].pop(key, None)
        _REPEAT_STATE["last_error"].pop(key, None)


def reset_repeat_guard() -> None:
    """Test/operator seam: clear the repeat ledger."""
    _REPEAT_STATE["fails"].clear()
    _REPEAT_STATE["last_error"].clear()


def route_tool(tool_name, args, config):
    if args is None:
        args = {}
    routes = _build_routes(config)

    # Safety-mode backstop (mode_hitl / mode_guardrail). The modes are also
    # described in the system prompt; this makes them impossible to bypass.
    blocked = check_mode_restrictions(tool_name, args, config)
    if blocked:
        return blocked

    # Engagement policy (suijin/policy.json): blocked tools/args and
    # out-of-scope targets. Opt-in — the default policy allows local ranges.
    from suijin.modules.ops.lib.governance import check_policy

    allowed, reason = check_policy(tool_name, args)
    if not allowed:
        return reason

    # ── FREEDOM: no phase gating. All tools always available. ──

    # Track recon actions (for informational purposes only)
    RECON_TOOLS = {
        "execute_terminal",
        "http_request",
        "search_cve",
        "search_kb",
        "read_file",
        "check_knowledge",
    }
    if tool_name in RECON_TOOLS:
        from suijin.modules.platform.lib.runtime import _recon_state

        _recon_state["exploration_count"] = _recon_state.get("exploration_count", 0) + 1

    if tool_name in routes:
        blocked_repeat = _repeat_guard_check(tool_name, args)
        if blocked_repeat:
            _REPEAT_STATE["blocked"] += 1
            return blocked_repeat
        result = _execute_with_healing(routes[tool_name], args, tool_name)
        _repeat_guard_record(tool_name, args, result)
        return _with_install_hint(tool_name, result)
    # Not found — suggest close matches; if none, point at the operator.
    # One guess maximum: guessing repeatedly burns the engagement.
    import difflib

    close = difflib.get_close_matches(tool_name, list(routes), n=3, cutoff=0.6)
    if close:
        return (
            f"TOOL NOT FOUND: {tool_name}. Closest matches: {', '.join(close)}. "
            "Use one of these, or if none is what you need, ASK THE OPERATOR "
            "(action: ask_operator) whether the tool exists."
        )
    return (
        f"TOOL NOT FOUND: {tool_name} — nothing similar is registered. Do NOT guess again. "
        "ASK THE OPERATOR (action: ask_operator, include your question) whether this tool "
        "exists or where to find it."
    )


def get_tool_catalog():
    """Return a formatted catalog of ALL available tools for the AI's system prompt.

    Dynamically includes core tools, Metasploit, CVE search, Oracle, notes,
    and any loaded module tools. Called from redteamer to build the prompt.
    """

    catalog = ""

    # ── MUST-USE TOOLS (these are NOT optional) ─────────────────────
    # Catalog diet: per-tool ```json blocks are kept ONLY for the four
    # mandatory doctrine tools — every other tool's args are already in
    # the complete registry rendering at the bottom, so examples here
    # were pure duplicate tokens.
    catalog += """## HOW TO CALL ANY TOOL (read this first)
Every tool below is called the same way — one JSON object per decision:
  {"action": "use_tool", "tool_name": "<name>", "args": {"<arg>": "<value>", ...}, "thought": "..."}
Tool names and their arguments are listed in ALL AVAILABLE TOOLS. Copy arg names exactly.

##  MANDATORY TOOLS — Use These Every Turn
- **write_note** — MANDATORY after EVERY action, EVERY tool result, EVERY phase transition — not just at milestones. If you ran a tool and did not write a note, your next decision is uninformed and the final report has a hole. Categories: recon, exploit, cve, blocked, finding, progress, complete. DO NOT SKIP.
  ```json
  {"tool": "write_note", "args": {"content": "Tested SQLi on /login with payload ' OR 1=1 --. Login bypass confirmed. Gained admin session.", "success": true, "category": "finding", "engagement": "target-name"}}
  ```
- **check_knowledge** — QUERY THE KNOWLEDGE GRAPH before EVERY payload attempt. It stores verified blocked patterns, WAF rules, and successful exploit vectors. Stop wasting cycles on known-blocked payloads.
  ```json
  {"tool": "check_knowledge", "args": {"target": "TARGET_HOST"}}
  ```
- **record_finding** — WRITE TO THE KNOWLEDGE GRAPH after EVERY confirmed result. SQLi works? Record it. WAF blocked something? Record it. CVE confirmed? Record it. This prevents duplicate work and builds institutional knowledge.
  ```json
  {"tool": "record_finding", "args": {"target": "TARGET", "finding_type": "verified_cve", "rule": "CVE-2021-41773 path traversal works on /cgi-bin/.%2e/%2e%2e/etc/passwd", "evidence": "Got /etc/passwd contents in response"}}
  ```
- **generate_report** — MANDATORY at engagement end. Creates detailed Markdown report with all findings, attack chains, Mermaid diagrams. Call BEFORE complete/claim_flag.
  ```json
  {"tool": "generate_report", "args": {"engagement": "target-name"}}
  ```
- **catalog_exploit** — MANDATORY the moment you find something valuable. Register it CLASSED: `severity` (critical/high/medium/low/info) + `cvss` (0.0-10.0) — records display as `CRITICAL CVSS 8.9 : SQL injection in the search parameter`. Write the exploit as a POC step-script (a list of `{"cmd": ..., "wait": N}` commands), give the `marker` that proves success, and call. THE SYSTEM RUNS THE POC BEFORE YOU CONTINUE. Perfect (marker reproduced) -> CONFIRMED, continue. Not perfect -> you get every command's output and THREE choices: (1) EDIT — re-call with `entry_id` + a fixed poc; (2) ABANDON — `entry_id` + `abandon:true` (the combo is memory-poisoned); (3) CLAIM IT WORKED ANYWAY — `entry_id` + `claim:true` (recorded AI_CLAIMED, amber-flagged 'NOT terminal-verified' in every report). A finding without a cataloged POC is a rumor.
  ```json
  {"tool": "catalog_exploit", "args": {"engagement": "t", "target": "http://t", "vuln_class": "sqli", "title": "login bypass", "poc": [{"cmd": "curl -s -c /tmp/j http://t/login -d 'u=x&p=y'", "wait": 1}, {"cmd": "curl -s -b /tmp/j 'http://t/admin?id=1 OR 1=1'"}], "marker": "root:", "guards": "needs low-priv session first"}}
  ```

## Core Tools (args in the ALL AVAILABLE TOOLS registry below)
- **execute_terminal** — Run ANY shell command. Use this for CLI tools: nmap, gobuster, ffuf, nikto, sqlmap, hydra, john, enum4linux, dirb, masscan, and any other pentesting tool installed on the system. Prefer dedicated CLI tools over raw curl/http_request for scanning and brute-forcing.
- **http_request** — Raw HTTP requests with full browser emulation. Use for manual web testing, not for scanning (use gobuster/nmap via execute_terminal instead).
- **bypass_403** — The 403 breaker: one call fires ~24 bypass variants (path normalization, X-Original-URL/XFF headers, method overrides, path-as-param) through http_request with pacing. Call it whenever a promising path 403s; the verdict table shows which variant got through.
- **http_replay** — THE governed send path for testing: payloads travel as DATA. Replay a stored request_id or inline spec through 15 mutation ops (add-query enables HPP, body-set-field dot-paths, set-method/target...) + 12 composable codecs (tab = WAF-evasion %09 spaces, url-double, base64, hex, html-dec, unicode...). `compare:{mutations,credential}` returns baseline + exploit + structured DIFF in ONE call — the 3-gate protocol (no measurable difference = NOT a finding). `credential:'name'` swaps auth wholesale (the IDOR/vertical-authz primitive). `sweep:{op,field,values}` tests ≤50 values paced. Every result carries a curl equivalent + DBMS error signatures. http_replay_raw sends VERBATIM bytes (smuggling/desync).
- **register_credential** / **list_credentials** — Named credential sets (auth headers + cookies) captured from logins you hold; the swap substrate for access-control replay.
- **web_session** — The cross-credential session model, built AUTOMATICALLY from every governed send: the access-control worklist (endpoint shapes reached by 2+ credentials, ID fields differing per credential — the IDOR substrate with the exact replay to fire) + hidden params (request fields the UI never exposed — mass-assignment targets). Role cycling: register credentials, replay the same surfaces as each, then action=summary.
- **inject_probe** — The evidence engine, NEVER an oracle: fires curated batteries (xss tag-survival + 20 weaponized payloads with sink-context classification; ssti 9-syntax product-discriminators — product-present + literal-absent = evaluated; cmd closed id/ver set; sqli DBMS error fingerprints + boolean pairs against a MEASURED noise floor; lfi file-signatures × 11 traversal shapes verbatim) and returns FACTS — surviving tags, reflection context, block signals ('WAF-blocked is NOT safe — escalate'), not_tested receipts. You craft the real exploit from the facts; confirm via catalog_exploit.
- **adjust_config** — Tune YOUR OWN run: no args shows the effective config; with args adjusts allowlisted keys (posture, temperature, max_tokens_per_request, provider, fallback_providers, model ids) — changes go LIVE on the next turn/call. Use it when the situation changes: provider dying (switch provider / extend fallback chain), recon exhausted (posture), responses truncated (max_tokens). Cost caps, stealth, safety modes, and scope are operator-only.
- **payload_mutate** — Evasion variants for a blocked payload: pass the payload (+ the blocked response) → ranked variants (case-rotation, inline comments, URL/double-URL/unicode encoding, whitespace, null-terminate) with family-escalation advice (reflected → blind → time-based → OOB). Fire variants one per request. THE answer to 'the payload worked manually but the WAF ate it'.
- **code_harness** — The exploit dev loop: write→run→triage→fix in a per-attempt sandbox. Args: goal, language (python/bash/php/go/js...), code, run_cmd ('{file}' placeholder), success_regex, fail_regex, timeout_s, max_cycles. Python gets mechanical fixes (auto pip-install, syntax catch). VERDICT: PASS is your EVIDENCE — record_finding on a code-based exploit claim REQUIRES a harness PASS in the same engagement; anything else is an unverified claim.
- **read_file** — Read any file on the system.
- **write_file** — Write files (scripts, payloads, notes). Defaults to suijin_agent/ for relative paths.
"""

    # Knowledge base — feature-gated: only advertised when the operator has
    # built it with `suijin pull kb`. Otherwise listed as disabled below.
    from suijin.modules.knowledge.lib.kb import kb_status

    _kb = kb_status()
    if _kb:
        per = ", ".join(f"{k} {v:,}" for k, v in sorted(_kb.get("per_source", {}).items()))
        catalog += f"""- **search_kb** — Full-text search the local knowledge base ({_kb["docs"]:,} docs: {per}). BM25-ranked results with snippets. Optional `source:<name>` filter (e.g. keyword "source:gtfobins awk sudo") and `limit` 1-20 (default 5). Prefer this over web_search for technique/payload/wordlist lookups — it is faster and offline.
- **suggest_exploit** — Offline exploit leads for a fingerprinted service: exact GTFOBins binary page + HackTricks + PayloadsAllTheThings hits. Run right after nmap/whatweb; follow with search_cve for exact-version CVEs.
- **find_wordlist** — Find SecLists wordlists by keyword AND materialize them into suijin_agent/wordlists/ for ffuf/gobuster/hydra.
- **extract_payloads** — Pull runnable code blocks from matching KB docs into suijin_agent/payloads/. Review before running.
- **kb_stats** — Knowledge base inventory: per-source doc counts, build age, failed sources.
"""

    catalog += """- **wordlist_tool** — Merge / dedupe / length-filter wordlists into suijin_agent/wordlists/.
- **mine_failures** — Cluster the failure DB so you never repeat a blocked technique/target combo.
- **anonymize_report** — Scrub IPs/emails/tokens/keys from a report file into suijin_agent/reports/anonymized/ before sharing.
- **apply_patch** — Patch vulnerabilities in the target lab application.
- **claim_flag** — Signal objective complete (args: flag).
- **recon_chain** — One-call recon: nmap scan + service fingerprint + version-based CVE lookup.

## Metasploit
- **msf_check** — Verify Metasploit availability.
- **msf_command** — Run raw msfconsole commands.
- **msf_run** — Execute exploit/auxiliary/post modules.
- **msf_sessions** — Manage sessions.

## Intelligence
- **search_cve** — Query NVD for CVEs by software+version.
- **check_knowledge** — Query the knowledge graph before generating payloads.
- **record_finding** — Persist verified findings.
- **write_note** — Log engagement progress (MANDATORY cadence — see top).

## Creative Freedom Tools
- **web_search** — Search the internet for exploit techniques, CVE details, documentation.
- **pip_install** — Install Python packages the agent needs (requests, pwntools, etc).
- **edit_skill** — Improve your own hacking methodology by editing skill prompts.
- **write_tool** — Create new Python tools to extend your capabilities.
- **list_skills** — See all attack skills you can edit.
- **list_own_files** — See all code files you can read and modify.

## Background Jobs (parallel execution)
- **job_spawn** happens automatically for slow tools (nmap, gobuster, sqlmap, hydra, ffuf, nikto).
  When you run these via execute_terminal, they return a job_id immediately. You keep working!
- **job_status** — Check status of a background job.
- **job_wait** — Wait for a job to complete (with timeout).
- **job_output** — Get full output from a completed job.
- **job_list** — List all running background jobs.
- **job_cancel** — Cancel a running job.
"""

    # Knowledge extras + evidence (B12-B15, B18)
    catalog += """## Knowledge & Evidence Tools
- **cve_advise_tools** — map a CVE/keyword to the tools that verify or exploit it.
- **kb_freshness** — KB age check; prompts a re-pull when stale.
- **evidence_capture** — seal a finding's evidence into the tamper-evident hash chain.
- **evidence_verify** — verify the evidence chain (tampering detection).
- **finding_dedup** — collapse same-root-cause findings into one with occurrences.
- **attack_paths** — probability-weighted attack-path scoring from findings.
"""

    # Fireteam (v5.1)
    catalog += """## Fireteam
- **fireteam_status** — check background specialist teams (deployed via action=deploy_subagent; results arrive automatically).
"""

    # Recipes section (A3)
    try:
        from suijin.modules.tools.lib.recipes import BUILT_IN_RECIPES

        if BUILT_IN_RECIPES:
            catalog += "## Tool Recipes (multi-tool macros)\n"
            for _rn, _steps in sorted(BUILT_IN_RECIPES.items()):
                catalog += (
                    f"- **recipe_run** ({_rn}: " + " -> ".join(st["tool"] for st in _steps) + ")\n"
                    f'  {{"tool": "recipe_run", "args": {{"name": "{_rn}", "target": "TARGET"}}}}\n'
                )
            catalog += "- **recipe_list** — every available recipe (built-in + user-defined).\n"
            catalog += "- **recipe_define** — persist your own macro as a JSON step list.\n"
    except Exception:  # noqa: BLE001
        pass

    # Addon tools section (zero-boilerplate drops)
    try:
        from suijin.modules.addons.entry import catalog_text

        _addon_cat = catalog_text()
        if _addon_cat:
            catalog += _addon_cat
    except Exception:  # noqa: BLE001
        pass

    # Analysis & utility routes that predate the curated prose above —
    # callable but previously invisible to the model (flexibility fix:
    # every routed tool must appear in the catalog exactly once).
    catalog += """## Analysis & Utility Tools (args in the registry below)
- **target_dossier** — per-target intelligence dossier (knowledge graph + failure history + notes).
- **payload_generate** — ready-made payloads by vulnerability type and framework.
- **mutate_wordlist** — expand a seed wordlist (leet, years, suffixes) into payloads/wordlists/.
- **cewl_words** — harvest a custom wordlist from a target URL's content.
- **diff_response** — diff baseline vs injected responses to expose subtle behavior changes.
- **rate_limit_check / rate_limit_all** — detect rate-limited endpoints before brute force.
- **attack_tree** — render a trace as an attack tree diagram for the report.
- **normalize_output** — normalize raw tool output for comparison/storage.
- **kb_read** — read one KB document by path (from search_kb results).
"""

    # ── THE KERNEL-RENDERED CAPABILITY SURFACE ──────────────────────
    # Every registered tool, rendered by the kernel from the LIVE
    # registry (Context.tool_reference) — what is registered is what
    # the agent sees; drift is impossible by construction. Falls back
    # to a manifest-derived rendering before any boot.
    try:
        from suijin.kernel.controller import last_context

        _ctx = last_context()
        reference = _ctx.tool_reference(core_first=("tools", "platform", "knowledge", "providers")) if _ctx else None
    except Exception:  # noqa: BLE001
        reference = None
    if reference is None:
        reference = _manifest_reference()
    catalog += "## ALL AVAILABLE TOOLS (complete — one line each)\n"
    catalog += "Every tool below is registered and callable RIGHT NOW. Args in parentheses.\n\n"
    catalog += reference + "\n\n"

    # ── skill docs: index only; detail via skill_read ────────────────
    try:
        from suijin.modules.skills.entry import skill_index

        _idx = skill_index()
        if _idx:
            catalog += "## PACK GUIDES\n" + _idx + "\nUse skill_read(pack) for full usage docs.\n\n"
    except Exception:  # noqa: BLE001
        pass

    # ── not-installed: one line, no wall ─────────────────────────────
    try:
        from suijin.modules.tools.lib.availability import missing_binaries

        _un = missing_binaries()
        if _un:
            catalog += f"## AVAILABILITY\n{len(_un)} tool(s) need binaries not installed on this host — calling them returns install hints; `suijin doctor` lists them.\n"
    except Exception:  # noqa: BLE001
        pass

    # KB feature gate: search_kb/kb_* degrade gracefully when not built
    if _kb is None:
        catalog += (
            "KB DISABLED — not built: search_kb, kb_stats, find_wordlist, suggest_exploit, "
            "extract_payloads return a 'not built' notice. Tell the operator to run `suijin pull kb`. "
            "Use web_search until then.\n\n"
        )

    catalog += """
## Attack Strategy (MUST FOLLOW)
1. **Recon and attack in parallel** — scanners (gobuster/nmap/nikto) run as BACKGROUND JOBS while you manually test every form, parameter, and endpoint already in front of you. Waiting for scans to finish before touching anything wastes the engagement.
2. **Knowledge base before attacking** — `search_kb` BEFORE every new attack technique, payload class, privesc path, or wordlist choice. If it says the KB is not built, tell the operator to run `suijin pull kb`.
3. **CVE before exploit** — `search_cve` after fingerprinting a service. Don't guess.
4. **Knowledge graph before payload** — `check_knowledge` before every new payload.
5. **Verify before claiming** — Confirm exploits with tool-call evidence. No hallucinations.
6. **Log everything** — `write_note` after every significant finding.
7. **Tool not found?** — ONE guess maximum, then ASK THE OPERATOR (action: ask_operator) where it is or whether it exists. Never guess repeatedly.
"""
    return catalog


def _manifest_reference() -> str:
    """Pre-boot fallback rendering (same shape as the kernel's
    tool_reference so the agent ALWAYS sees tools, even before any
    boot): pack manifests + every dispatch route not pack-owned."""
    from suijin.modules.loader import discover_modules, get_loaded_modules, get_module_tools

    discover_modules()
    mods = get_loaded_modules() or {}
    lines = []
    covered = set()
    for key in sorted(mods):
        tools = mods[key].get("manifest", {}).get("tools") or {}
        if not tools:
            continue
        lines.append(f"[{key}]")
        for name, t in sorted(tools.items()):
            covered.add(name)
            params = list((t.get("parameters") or {}) if isinstance(t, dict) else {})
            sig = f"{name}({', '.join(params)})" if params else f"{name}()"
            one = " ".join(str(t.get("description", "") if isinstance(t, dict) else "").split())[:90]
            lines.append(f"- {sig} — {one}")
    # core dispatch routes the packs don't own (deploy_subagent etc.) —
    # the contract is zero invisible tools in EVERY rendering mode
    leftover = sorted(set(list_route_tools()) - covered - set(get_module_tools()))
    if leftover:
        lines.append("[core]")
        lines += [f"- {n}()" for n in leftover]
    return "\n".join(lines)
