"""Suijin command-line entry point.

`suijin`                 -> launch the classic Rich TUI
`suijin doctor`          -> verify the environment is ready
`suijin status`          -> one-page system summary
`suijin selftest`        -> offline smoke test (no network, no keys)
`suijin version`         -> release / python / package details
`suijin env`             -> API key presence (names only)
`suijin tools`           -> all agent tools + availability
`suijin modules`         -> loaded module packs
`suijin skills`          -> agent-editable skills
`suijin config show`     -> effective config (secrets redacted)
`suijin config validate` -> Pydantic validation of both configs
`suijin workspace`       -> workspace layout + usage + symlink health
`suijin reports`         -> engagement reports
`suijin sessions`        -> saved sessions
`suijin labs`            -> built-in vulnerable labs with ports
`suijin pull kb ...`     -> build/inspect the offline knowledge base

Every subcommand is non-interactive and scriptable (exit 0 = healthy).
"""

import argparse
import importlib
import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path

# Make the repo root importable so `from suijin import ...` works regardless of
# where this script lives (source checkout or installed into ~/.suijin/repo).
_pkg_parent = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)  # lib/console/modules/suijin/repo
if _pkg_parent not in sys.path:
    sys.path.insert(0, _pkg_parent)

# Single source of truth: suijin/version.json (via the package __init__).
from suijin.modules.platform.lib.workspace import artifact_dir as _ad  # noqa: E402 — console surface (boundary-exempt)

VERSION = None  # patchable seam; resolved lazily


def _ver():
    v = globals().get("VERSION")
    if v is not None:
        return v
    from suijin import __version__ as _v

    return _v


_PKG_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)  # suijin/ package

REQUIRED_BINARIES = ["nmap", "gobuster", "feroxbuster", "john", "curl"]
OPTIONAL_BINARIES = [
    "sqlmap",
    "hydra",
    "amass",
    "subfinder",
    "httpx",
    "nuclei",
    "katana",
    "nikto",
    "whatweb",
    "sslscan",
    "ffuf",
    "msfconsole",
]
CORE_IMPORTS = ["rich", "flask", "flask_cors", "langgraph", "pydantic", "requests", "urllib3"]
LAB_PORT = 5906


def _ok(name, detail=""):
    return ("PASS", name, detail)


def _warn(name, detail=""):
    return ("WARN", name, detail)


def _fail(name, detail=""):
    return ("FAIL", name, detail)


def run_doctor() -> int:
    from suijin.modules.platform.lib.runtime import init_runtime

    init_runtime()
    rows = []
    critical = 0

    # Python version
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 10):
        rows.append(_ok("python", py))
    else:
        rows.append(_fail("python", f"{py} (need 3.10+)"))
        critical += 1

    # Core dependencies
    missing_deps = [m for m in CORE_IMPORTS if not _importable(m)]
    if not missing_deps:
        rows.append(_ok("dependencies", f"{len(CORE_IMPORTS)} core packages"))
    else:
        rows.append(_fail("dependencies", "missing: " + ", ".join(missing_deps)))
        critical += 1

    # KG backend status — which storage answers (json default / neo4j switch)
    try:
        from suijin.modules.redteam.lib.intel.kg_backend import backend_status

        rows.append(_ok("knowledge graph", backend_status()))
    except Exception as e:
        rows.append(_warn("knowledge graph", str(e)[:60]))

    # Provider failover telemetry (D29) — informational
    try:
        from suijin.modules.providers.lib import FAILOVER_STATS as _fs

        if _fs["chains"]:
            note = f"{_fs['chains']} chains: {_fs['primary_ok']} primary-ok, {_fs['failovers']} failovers, {_fs['all_down']} all-down"
            if _fs["last_event"]:
                note += f" | last: {_fs['last_event'][:60]}"
            rows.append(_ok("provider failover", note))
        else:
            rows.append(_ok("provider failover", "no chain calls yet this session"))
    except Exception as e:
        rows.append(_warn("provider failover", str(e)[:60]))

    # Required binaries
    for b in REQUIRED_BINARIES:
        p = shutil.which(b)
        if p:
            rows.append(_ok(f"bin/{b}", p))
        else:
            rows.append(_fail(f"bin/{b}", "not found on PATH"))
            critical += 1

    # Optional binaries
    for b in OPTIONAL_BINARIES:
        p = shutil.which(b)
        rows.append(_ok(f"bin/{b}", p) if p else _warn(f"bin/{b}", "not installed (optional)"))

    # Config — lives inside the suijin/ package dir, not the repo root.
    cfg = os.path.join(_PKG_DIR, "config.json")
    if os.path.exists(cfg):
        try:
            import json

            with open(cfg) as f:
                data = json.load(f)
            has_key = _has_any_api_key(_PKG_DIR)
            provider = data.get("provider", "unset")
            detail = f"provider={provider}"
            if provider == "zai":
                endpoint = data.get("zai_endpoint") or "coding"
                detail += f", endpoint={endpoint} ({'Coding Plan quota' if endpoint == 'coding' else 'pay-as-you-go'})"
            if has_key:
                rows.append(_ok("config", f"{detail}, api key set"))
            else:
                rows.append(_warn("config", f"{detail}, no api key (heuristic mode works)"))
        except Exception as e:
            rows.append(_warn("config", f"unreadable: {e}"))
    else:
        rows.append(_warn("config", "no config.json (heuristic mode works)"))

    # Lab port
    if _port_free(LAB_PORT):
        rows.append(_ok("lab", f"port {LAB_PORT} free"))
    else:
        rows.append(_warn("lab", f"port {LAB_PORT} already in use"))

    # Module packs
    try:
        from suijin.modules.loader import discover_modules, get_module_tools

        discover_modules()
        n = len(get_module_tools())
        rows.append(_ok("modules", f"{n} module tools loaded"))
    except Exception as e:
        rows.append(_fail("modules", str(e)))
        critical += 1

    # Knowledge base (optional — built on demand via `suijin pull kb`)
    try:
        from suijin.modules.knowledge.lib.kb import kb_status

        st = kb_status()
        if st:
            per = ", ".join(f"{k}:{v:,}" for k, v in sorted(st.get("per_source", {}).items()))
            detail = f"{st['docs']:,} docs / {st['sources']} sources (built {st['built_at'][:10]})"
            if per:
                detail += f" [{per}]"
            if st.get("age_days") is not None and st["age_days"] > 30:
                detail += f" — STALE ({st['age_days']}d old, rerun: suijin pull kb --force)"
                rows.append(_warn("knowledge base", detail))
            else:
                rows.append(_ok("knowledge base", detail))
        else:
            rows.append(_warn("knowledge base", "not built — run: suijin pull kb"))
    except Exception as e:
        rows.append(_warn("knowledge base", str(e)))

    # Workspace layout (canonical root dir + inner symlink)
    try:
        import suijin.modules.platform.lib.workspace as ws

        ws.ensure_workspace_layout()
        inner = ws.PROJECT_DIR / "suijin" / "suijin_agent"
        if inner.is_symlink():
            rows.append(_ok("workspace", f"{ws.WORKSPACE_DIR} (symlink ok)"))
        else:
            rows.append(_warn("workspace", "suijin/suijin_agent is not a symlink -> ../suijin_agent"))
    except Exception as e:
        rows.append(_warn("workspace", str(e)))

    # Print
    print("Suijin doctor v" + _ver())
    print("-" * 56)
    for status, name, detail in rows:
        mark = {"PASS": "ok", "WARN": "!!", "FAIL": "XX"}[status]
        print(f"  [{mark}] {name:14} {detail}")
    print("-" * 56)
    if critical:
        print(f"\n{critical} critical problem(s). Fix them and re-run 'suijin doctor'.")
        return 1
    print("\nReady. Run 'suijin' to start the interface.")
    return 0


def _has_any_api_key(pkg_dir: str) -> bool:
    """True if any supported provider key is set (env var or suijin/.env).

    API keys live in .env / environment variables — never in config.json.
    """
    env_names = (
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "HF_TOKEN",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AMD_API_KEY",
        "ZAI_API_KEY",
    )
    if any(os.environ.get(n) for n in env_names):
        return True
    env_file = os.path.join(pkg_dir, ".env")
    try:
        with open(env_file) as f:
            for line in f:
                name, _, value = line.partition("=")
                if name.strip() in env_names and value.strip():
                    return True
    except OSError:
        pass
    return False


def _importable(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def _port_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", port)) != 0
    except Exception:
        return False


def run_pull_kb(args) -> int:
    """`suijin pull kb` — download & compile the security knowledge base."""
    from suijin.modules.knowledge.lib.kb import DB_PATH, SOURCES, compile_kb, kb_status

    if getattr(args, "list_sources", False):
        print("Available knowledge base sources:")
        for name, cfg in SOURCES.items():
            note = f"  [{cfg['note']}]" if cfg.get("note") else ""
            print(f"  {name:12} {cfg['repo']}  ({', '.join(cfg['patterns'])}){note}")
        return 0

    if getattr(args, "status", False):
        st = kb_status()
        if not st:
            print("Knowledge base NOT BUILT — knowledge base features are DISABLED.")
            print("Enable them with: suijin pull kb")
            return 1
        per = st.get("per_source", {})
        print(
            f"Knowledge base: {st['docs']:,} docs / {st['sources']} sources "
            f"(built {st['built_at'][:19].replace('T', ' ')})"
        )
        print(
            f"  db: {DB_PATH} ({st['size_bytes'] / 1_048_576:.1f} MB, {'FTS5' if st.get('fts5') else 'LIKE fallback'})"
        )
        for name in sorted(per):
            print(f"    {name:12} {per[name]:,} docs")
        for name in sorted(st.get("failed", {})):
            print(f"    {name:12} FAILED at last build — retry: suijin pull kb --sources {name}")
        if st.get("age_days") is not None and st["age_days"] > 30:
            print(f"  stale: built {st['age_days']} days ago — refresh with: suijin pull kb --force")
        print("Knowledge base features are ENABLED (search_kb available to the agent).")
        return 0

    sources = getattr(args, "sources", None) or None
    try:
        summary = compile_kb(sources=sources, force=getattr(args, "force", False))
    except ValueError as e:
        print(f"error: {e}")
        return 2
    except Exception as e:
        print(f"error: {e}")
        return 1
    total = summary.pop("_total", 0)
    fts = summary.pop("_fts5", False)
    failed = summary.pop("_failed", {})
    print("\nKnowledge base compiled:")
    for name, count in summary.items():
        print(f"  {name:12} {count:,} docs")
    print(f"  {'TOTAL':12} {total:,} docs (full-text search: {'FTS5' if fts else 'LIKE fallback'})")
    if failed:
        print("\nWARNING: some sources failed (cached tarballs of the rest are kept):")
        for name, err in failed.items():
            print(f"  {name:12} {err}")
        print("Re-run later: suijin pull kb --sources " + " ".join(failed))
        print("Knowledge base PARTIALLY ENABLED — failed sources above are not searchable.")
    else:
        print("Knowledge base ENABLED — search_kb is now available to the agent.")
    return 0


# ── Non-interactive info commands ─────────────────────────────────────
# All offline, all safe to script: `suijin status && suijin labs` etc.

ENV_KEY_NAMES = (
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "HF_TOKEN",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AMD_API_KEY",
    "ZAI_API_KEY",
    "NVD_API_KEY",  # optional — raises NVD rate limits for search_cve
)


def run_version() -> int:
    """Detailed version info: release, python, platform, package location."""
    import json
    import platform

    codename = release = ""
    try:
        with open(os.path.join(_PKG_DIR, "version.json")) as f:
            vj = json.load(f)
        codename, release = vj.get("codename", ""), vj.get("release_date", "")
    except (OSError, ValueError):
        pass
    print(f"suijin {_ver()}" + (f'  "{codename}"' if codename else "") + (f"  ({release})" if release else ""))
    print(f"python {platform.python_version()} on {platform.system()} {platform.machine()}")
    print(f"package: {_PKG_DIR}")
    return 0


def run_env() -> int:
    """Show API-key presence by name only — values are NEVER printed."""
    file_keys = set()
    try:
        with open(os.path.join(_PKG_DIR, ".env")) as f:
            for line in f:
                name, _, value = line.partition("=")
                if name.strip() and value.strip():
                    file_keys.add(name.strip())
    except OSError:
        pass
    print("API keys (names only — values never shown):")
    for name in ENV_KEY_NAMES:
        if os.environ.get(name):
            where = "environment"
        elif name in file_keys:
            where = "suijin/.env"
        else:
            where = ""
        print(f"  {name:20} {'SET' if where else 'not set':8}" + (f"({where})" if where else ""))
    return 0


def run_status() -> int:
    """One-page system summary: provider, KB, workspace, modules, lab port."""
    import json

    print(f"suijin {_ver()}")
    try:
        with open(os.path.join(_PKG_DIR, "config.json")) as f:
            cfg = json.load(f)
        provider = cfg.get("provider", "deepseek")
        line = f"provider:         {provider} — api key {'set' if _has_any_api_key(_PKG_DIR) else 'NOT set (heuristic mode works)'}"
        if provider == "zai":
            ep = cfg.get("zai_endpoint") or "coding"
            line += f" | endpoint: {ep}"
        print(line)
    except OSError:
        print("provider:         no config.json (heuristic mode works)")

    try:
        from suijin.modules.knowledge.lib.kb import kb_status

        st = kb_status()
        if st:
            age = f", {st['age_days']}d old" if st.get("age_days") is not None else ""
            print(f"knowledge base:   {st['docs']:,} docs / {st['sources']} sources{age}")
        else:
            print("knowledge base:   NOT built — run: suijin pull kb")
    except Exception as e:
        print(f"knowledge base:   {e}")

    try:
        import suijin.modules.platform.lib.workspace as ws

        ws.ensure_workspace_layout()
        inner = ws.PROJECT_DIR / "suijin" / "suijin_agent"
        print(f"workspace:        {ws.WORKSPACE_DIR}" + ("" if inner.is_symlink() else "  (!! symlink missing)"))
    except Exception as e:
        print(f"workspace:        {e}")

    try:
        from suijin.modules.loader import discover_modules, get_module_tools

        discover_modules()
        print(f"modules:          {len(get_module_tools())} module tools loaded")
    except Exception as e:
        print(f"modules:          load failed — {e}")

    print(f"lab port:         5906 {'free' if _port_free(5906) else 'IN USE'}")

    # Efficiency leaderboard + forecast (D28/D30)
    try:
        from suijin.modules.ops.lib.metering import forecast, leaderboard

        print()
        print(leaderboard(limit=8))
        fc = forecast()
        if fc:
            print(f"forecast: {fc.splitlines()[0]}")
    except Exception as e:
        print(f"metering:  {e}")
    return 0


def run_tools_list() -> int:
    """Every callable agent tool, core + module, with availability marks."""
    from suijin.modules.tools.lib.dispatch import list_route_tools

    core = sorted(list_route_tools())
    print(f"Core tools ({len(core)}):")
    for t in core:
        print(f"  {t}")
    try:
        from suijin.modules.loader import discover_modules, get_loaded_modules
        from suijin.modules.tools.lib.availability import missing_binaries

        discover_modules()
        unavail = missing_binaries()
        mods = get_loaded_modules() or {}
        total = shown = 0
        print("\nModule tools:")
        for mod_name in sorted(mods):
            tools = mods[mod_name].get("manifest", {}).get("tools", {})
            for t_name in sorted(tools):
                total += 1
                if t_name in unavail:
                    print(f"  {t_name:24} [missing: {', '.join(unavail[t_name])}]")
                else:
                    shown += 1
                    print(f"  {t_name}")
        print(f"\n{total} module tools ({shown} ready, {total - shown} missing binaries)")
    except Exception as e:
        print(f"\nModule tools: unavailable — {e}")
    return 0


def run_modules_list() -> int:
    """Loaded module packs with tool counts and binary dependencies."""
    from suijin.modules.loader import discover_modules, get_loaded_modules

    discover_modules()
    mods = get_loaded_modules() or {}
    if not mods:
        print("No module packs found (vendored packs live in the package; user packs under ~/.suijin/modules).")
        return 1
    total_tools = 0
    print(f"{len(mods)} module packs:")
    for name in sorted(mods):
        manifest = mods[name].get("manifest", {})
        tools = manifest.get("tools", {})
        deps = manifest.get("dependencies", [])
        total_tools += len(tools)
        line = f"  {name:22} {len(tools)} tool{'s' if len(tools) != 1 else ''}"
        if deps:
            line += f"  (requires: {', '.join(deps)})"
        print(line)
    print(f"\n{total_tools} tools total")
    return 0


def run_skills_list() -> int:
    """Attack/defense skills the agent can edit via the edit_skill tool."""
    from suijin.modules.tools.lib.self_improve import list_available_skills

    out = list_available_skills()
    print(out)
    return 0


# Keys whose VALUES must never reach a terminal. config.json should not hold
# secrets (keys live in .env), but redact defensively anyway.
_SECRET_MARKERS = ("key", "token", "secret", "password", "credential")


def _redact(obj):
    if isinstance(obj, dict):
        return {
            k: ("***redacted***" if any(m in k.lower() for m in _SECRET_MARKERS) and v else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def _effective_config() -> dict:
    from suijin.modules.redteam.lib.red.config_loader import load_config

    return load_config()


def run_config_show() -> int:
    """Effective red-team config (defaults merged), secrets redacted."""
    import json

    print(json.dumps(_redact(_effective_config()), indent=2, sort_keys=True))
    return 0


def run_config_validate() -> int:
    """Pydantic-validate config.json and blue_config.json. Exit 1 on failure."""
    import json

    from suijin.modules.platform.lib.config_models import BlueConfig, RedConfig

    ok = True
    checks = (
        ("config.json", RedConfig),
        ("blue_config.json", BlueConfig),
    )
    # config.json = package-level operator config (API keys; the compose
    # mount point). blue_config.json = workspace operator tuning (v4.1).
    from suijin.modules.blueteam.lib.blue import config as _blue_cfg

    for fname, model in checks:
        path = str(_blue_cfg._config_path()) if fname == "blue_config.json" else os.path.join(_PKG_DIR, fname)
        if not os.path.exists(path):
            print(f"[--] {fname}: not present (defaults apply)")
            continue
        try:
            with open(path) as f:
                model(**json.load(f))
            print(f"[ok] {fname}: valid")
        except Exception as e:
            ok = False
            print(f"[XX] {fname}: INVALID — {e}")
    return 0 if ok else 1


def _dir_stats(p) -> tuple[int, int]:
    n = s = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                n += 1
                s += f.stat().st_size
        except OSError:
            pass
    return n, s


def run_workspace_status() -> int:
    """Canonical workspace layout, per-directory usage, symlink health."""

    import suijin.modules.platform.lib.workspace as ws

    ws.ensure_workspace_layout()
    inner = ws.PROJECT_DIR / "suijin" / "suijin_agent"
    print(f"workspace: {ws.WORKSPACE_DIR}")
    print(
        f"symlink:   suijin/suijin_agent -> "
        f"{'../suijin_agent (ok)' if inner.is_symlink() else 'MISSING — run: suijin selftest'}"
    )
    total = 0
    if ws.WORKSPACE_DIR.exists():
        for entry in sorted(ws.WORKSPACE_DIR.iterdir()):
            if entry.is_dir():
                n, size = _dir_stats(entry)
                total += size
                print(f"  {entry.name + '/':<18} {n:>5} files  {size / 1024:>9.0f} KB")
            else:
                size = entry.stat().st_size
                total += size
                print(f"  {entry.name:<18} {'':>11}  {size / 1024:>9.0f} KB")
    print(f"  {'total':<18} {'':>11}  {total / 1024 / 1024:>9.1f} MB")
    return 0 if inner.is_symlink() else 1


def run_reports_list() -> int:
    """Engagement reports in suijin_agent/reports (newest first, top 30)."""
    from datetime import datetime

    rdir = _ad("reports")
    files = []
    if rdir.exists():
        files = sorted((f for f in rdir.rglob("*") if f.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
    if not files:
        print("No reports yet — they land in suijin_agent/reports/ after an engagement.")
        return 0
    print(f"Reports in {rdir} (newest first):")
    for f in files:
        st = f.stat()
        when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {when}  {st.st_size / 1024:>8.0f} KB  {f.relative_to(rdir)}")
    return 0


def run_sessions_list() -> int:
    """Saved engagement sessions in suijin_agent/sessions (newest first)."""
    import json
    from datetime import datetime

    sdir = _ad("sessions")
    files = []
    if sdir.exists():
        files = sorted(sdir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        print("No saved sessions — suijin_agent/sessions/ fills up during engagements.")
        return 0
    print(f"{len(files)} saved sessions (newest first):")
    for f in files:
        st = f.stat()
        when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        objective = ""
        try:
            with open(f) as fh:
                objective = (json.load(fh).get("objective") or "")[:60]
        except (OSError, ValueError):
            pass
        suffix = f"  {objective}" if objective else ""
        print(f"  {when}  {f.name}{suffix}")
    return 0


def run_export(args) -> int:
    """`suijin export` — chain-of-custody evidence bundle."""
    from suijin.modules.ops.lib.export_bundle import build_bundle, verify_bundle

    verify = getattr(args, "verify", None)
    if verify:
        ok, problems = verify_bundle(Path(verify))
        if ok:
            print(f"[ok] {verify}: all manifest hashes verified")
            return 0
        for p in problems:
            print(f"[XX] {p}")
        return 1
    out = build_bundle(
        out_path=Path(args.out) if getattr(args, "out", None) else None,
        include_credentials=bool(getattr(args, "with_creds", False)),
    )
    ok, problems = verify_bundle(out)
    status = "verified" if ok else f"PROBLEM: {problems}"
    print(f"evidence bundle: {out}")
    print(f"  integrity: {status}")
    print(f"  credentials: {'INCLUDED (sensitive!)' if getattr(args, 'with_creds', False) else 'excluded'}")
    return 0 if ok else 1


def run_debrief(args) -> int:
    """`suijin debrief` — engagement analytics from audit trails."""
    from suijin.modules.ops.lib.debrief import load_audits, render_debrief

    trails = load_audits()
    print(render_debrief(trails, verbose=bool(getattr(args, "verbose", False))))
    return 0


def run_replay(args) -> int:
    """`suijin replay` — step through an engagement timeline."""
    from suijin.modules.ops.lib import replay as rp

    if getattr(args, "list_replays", False):
        trails = rp.list_replays()
        if not trails:
            print("No replayable engagements (need iterations in audit_trails).")
            return 1
        for t in trails:
            n = len(t.get("iterations", []))
            print(f"  {t.get('_file', ''):44} {t.get('engagement', '?'):24} {n:>4} steps")
        return 0

    f = getattr(args, "file", None)
    if f:
        path = Path(f)
        trail = None
        if not path.is_absolute():
            cand = _ad("audit_trails") / f
            path = cand if cand.exists() else path
        if path.exists():
            try:
                trail = json.loads(path.read_text())
                trail["_file"] = path.name
            except ValueError:
                print(f"error: {f} is not valid JSON")
                return 1
        if trail is None:
            print(f"error: no such audit trail: {f}")
            return 1
    else:
        trail = rp.pick_engagement()
        if trail is None:
            return 1

    if getattr(args, "export_md", None):
        md = rp.render_markdown(trail)
        out = Path(args.export_md)
        out.write_text(md)
        print(f"transcript exported: {out}")
        return 0

    rp.run_replay(trail)
    return 0


def run_eval(args) -> int:
    """`suijin eval` — replay recorded traffic through the blue detector."""
    from suijin.modules.blueteam.lib.blue.traffic.replay_harness import (
        label_entries,
        render_eval,
        replay_scores,
    )
    from suijin.modules.platform.lib.constants import BLUE_TRAFFIC_LOG

    log = Path(getattr(args, "traffic", None) or BLUE_TRAFFIC_LOG)
    if not log.exists():
        print(f"no traffic log at {log} — run the blue lab or point --traffic at a .jsonl file")
        return 1
    entries = []
    for line in log.read_text(errors="ignore").splitlines():
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    if len(entries) < 5:
        print(f"only {len(entries)} entries in {log} — need at least 5 (baseline + eval)")
        return 1
    labels_path = Path(args.labels) if getattr(args, "labels", None) else None
    labels = label_entries(entries, labels_path)
    scores = replay_scores(entries)
    print(
        render_eval(
            labels,
            scores,
            default_threshold=int(getattr(args, "threshold", 5)),
            do_sweep=not getattr(args, "no_sweep", False),
        )
    )
    return 0


def run_real_battle_cmd(args) -> int:
    """`suijin battle --real|--mock` — the actual red agent vs the live lab."""
    from suijin.modules.ops.lib.real_battle import render_real_verdict, run_real_battle

    mock = not getattr(args, "real", False)  # default --mock semantics unless --real
    port = getattr(args, "port", 0) or 0
    if port == 5906:
        port = None  # default scripted port not for real battles
    v = run_real_battle(port=port, mock=mock, objective=getattr(args, "objective", "") or "")
    print(render_real_verdict(v))
    return 0


def run_bench_cmd(args) -> int:
    """`suijin bench` — graded lab runs: flags/tools/cost score per release."""
    from suijin.modules.ops.lib.bench import render_history, run_all, run_bench

    if getattr(args, "history", False):
        print(render_history())
        return 0
    live = bool(getattr(args, "live", False))
    lab = getattr(args, "lab", "") or ""
    scores = [run_bench(lab, mock=not live)] if lab else run_all(mock=not live)
    ok = True
    for s in scores:
        if "error" in s:
            ok = False
            print(f"bench error ({s.get('lab', '?')}): {s['error']}")
            continue
        print(
            f"{s['lab']:12} {s['mode']:4} flags {s['flags_captured']}/{s['flags_known']} "
            f"({s['capture_rate']:.0%})  calls {s['tool_calls']}  iters {s['iterations']}  cost ${s['cost_usd']:.4f}"
        )
        if s["flags_detail"]:
            print("  captured: " + ", ".join(s["flags_detail"]))
    print("\nhistory: suijin bench --history")
    return 0 if ok else 1


def run_battle_cmd(args) -> int:
    """`suijin battle` — purple team: scripted red vs pattern blue, live scoreboard."""
    if getattr(args, "real", False) or getattr(args, "mock", False):
        return run_real_battle_cmd(args)
    from suijin.modules.ops.lib.battle import run_battle

    result = run_battle(port=int(getattr(args, "port", 0) or 5906))
    print(
        f"\nred {result['red_score']} — blue {result['blue_score']} | "
        f"flags {len(result['flags'])} | detected {result['detected']} | "
        f"tarpits {result['tarpitted']} | blocked {result['blocked']}"
    )
    return 0


def _first_docstring(path: str) -> str:
    import re

    try:
        with open(path) as f:
            head = f.read(4000)
        m = re.search(r'"""(.+?)"""', head, re.DOTALL)
        if m:
            return " ".join(m.group(1).split())
    except OSError:
        pass
    return ""


def _lab_port(path: str) -> str:
    """Best-effort port extraction: `port=NNNN` anywhere, else docstring hints."""
    import re

    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return "?"
    m = re.search(r"port\s*=\s*(\d{4,5})", text)
    if not m:
        # docstring hints like "Port 5906" / "Port: 5700"
        m = re.search(r"[Pp]ort[:\s]+(\d{4,5})", text[:4000])
    return m.group(1) if m else "?"


def run_labs_list() -> int:
    """Built-in vulnerable labs: ports, descriptions, launch commands."""
    lab_dir = os.path.join(_PKG_DIR, "lab")
    found = 0
    for name in sorted(os.listdir(lab_dir)):
        d = os.path.join(lab_dir, name)
        if not os.path.isdir(d) or name.startswith("__"):
            continue
        app = next((c for c in ("app.py", "vulnerable_app.py") if os.path.exists(os.path.join(d, c))), None)
        if not app:
            continue
        found += 1
        port = _lab_port(os.path.join(d, app))
        suffix = ""
        if port != "?":
            suffix = "" if _port_free(int(port)) else "  (IN USE)"
        print(f"  {name:18} :{port:<5} python3 suijin/lab/{name}/{app}{suffix}")
        doc = _first_docstring(os.path.join(d, app))
        if doc:
            print(f"  {'':18} {doc}")
    if not found:
        print("No labs found under suijin/lab/.")
        return 1
    print("\nStart one, then point Red Team at http://127.0.0.1:<port>.")
    return 0


def run_selftest() -> int:
    """Offline smoke test: imports, KB gating, workspace anchors, sandbox.

    No network, no API keys, no side effects beyond workspace layout repair
    (which runs on every import of the platform runtime anyway).
    """
    from unittest.mock import patch

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, fn):
        try:
            detail = fn()
            checks.append((name, True, detail or "ok"))
        except Exception as e:
            checks.append((name, False, str(e)))

    def _imports():
        import suijin.modules.knowledge.lib.kb  # noqa: F401
        import suijin.modules.tools.lib.dispatch  # noqa: F401 — pulls runtime, workspace, kb

        return "ok"

    def _kb_status():
        from suijin.modules.knowledge.lib.kb import kb_status

        st = kb_status()
        if st:
            return f"built — {st['docs']:,} docs / {st['sources']} sources, search_kb ENABLED"
        return "not built — search_kb DISABLED (run: suijin pull kb)"

    def _catalog_gating():
        import suijin.modules.knowledge.lib.kb as kb_mod
        from suijin.modules.knowledge.lib.kb import DB_PATH as real_db
        from suijin.modules.tools.lib import dispatch

        built = dispatch.get_tool_catalog()
        with patch.object(kb_mod, "DB_PATH", real_db.parent / "_selftest_missing_.sqlite3"):
            missing = dispatch.get_tool_catalog()
        st = kb_mod.kb_status()
        if st:
            assert "**search_kb**" in built, "catalog must advertise search_kb when built"
        else:
            assert "DISABLED" in built, "catalog must list search_kb as DISABLED when not built"
        assert "DISABLED" in missing and "suijin pull kb" in missing
        return "catalog gating consistent (built + disabled states)"

    def _workspace_anchor():
        from suijin.modules.platform.lib.workspace import PROJECT_DIR, WORKSPACE_DIR, ensure_workspace_layout

        assert WORKSPACE_DIR == PROJECT_DIR / "suijin_agent"
        ensure_workspace_layout()  # repair if needed, then verify
        inner = PROJECT_DIR / "suijin" / "suijin_agent"
        assert inner.is_symlink() or not inner.exists(), (
            f"suijin/suijin_agent must be a symlink to ../suijin_agent (got {inner})"
        )
        return f"{WORKSPACE_DIR} (suijin/suijin_agent -> ../suijin_agent)"

    def _sandbox():
        from pathlib import Path

        from suijin.modules.platform.lib.infra import job_runner
        from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

        wd = Path(job_runner.get_sandbox_workdir())
        assert str(wd).startswith(str(WORKSPACE_DIR)), f"sandbox escaped workspace: {wd}"
        assert not job_runner.is_command_allowed("shutdown -h now")
        assert job_runner.is_command_allowed("nmap -sV 127.0.0.1")
        return "sandbox inside workspace, guardrails block system commands"

    def _boundary():
        from suijin.modules.platform.lib.workspace import resolve_workspace_path

        try:
            resolve_workspace_path("/etc/passwd")
        except PermissionError:
            return "writes confined to suijin_agent/ + allowlist"
        raise AssertionError("absolute path outside workspace was not rejected")

    def _modules():
        from suijin.modules.loader import discover_modules, get_module_tools

        discover_modules()
        return f"{len(get_module_tools())} module tools loaded"

    check("core imports", _imports)
    check("kb status", _kb_status)
    check("kb gating", _catalog_gating)
    check("workspace", _workspace_anchor)
    check("sandbox", _sandbox)
    check("boundary", _boundary)
    check("modules", _modules)

    print("Suijin selftest v" + _ver())
    print("-" * 56)
    failed = 0
    for name, ok, detail in checks:
        mark = "ok" if ok else "XX"
        if not ok:
            failed += 1
        print(f"  [{mark}] {name:12} {detail}")
    print("-" * 56)
    if failed:
        print(f"\n{failed} check(s) FAILED.")
        return 1
    print("\nAll checks passed. Offline plumbing is healthy.")
    return 0


# ── v2.10 operator commands ───────────────────────────────────────────


def run_kb_read(args) -> int:
    from suijin.modules.knowledge.lib.kb import read_doc

    try:
        source, path, content = read_doc(args.path, source=getattr(args, "source", None))
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}")
        return 1
    print(f"--- [{source}] {path} ({len(content):,} chars) ---")
    print(content)
    return 0


def run_kb_diff(_args) -> int:
    from suijin.modules.knowledge.lib.kb import kb_diff

    d = kb_diff()
    if not d["built"]:
        print("Knowledge base NOT BUILT — run: suijin pull kb")
        return 1
    print(f"built: {str(d['built_at'])[:19]}")
    actions = []
    for name, row in d["sources"].items():
        flag = {"rebuild": "[rebuild]", "pull": "[pull]  ", "cache missing": "[nocach]"}.get(row["action"], "[ok]    ")
        idx = f"{row['indexed_docs']:,} docs" if row["indexed_docs"] is not None else "not indexed"
        newer = " cache-newer" if row["cache_newer_than_build"] else ""
        print(f"  {flag} {name:12} {idx}{newer}")
        if row["action"] in ("rebuild", "pull"):
            actions.append(name)
    if actions:
        print(f"\nstale/missing: {', '.join(actions)} — refresh with: suijin pull kb --sources {' '.join(actions)}")
    else:
        print("\nindex is up to date with cached tarballs.")
    return 0


def run_pull_cve(args) -> int:
    from suijin.modules.knowledge.lib.cve_mirror import kev_status, pull_kev

    if getattr(args, "status", False):
        st = kev_status()
        if not st:
            print("KEV mirror not built — run: suijin pull cve")
            return 1
        print(f"KEV mirror: {st['count']} actively-exploited CVEs (retrieved {str(st['retrieved'])[:19]})")
        return 0
    try:
        data = pull_kev(force=bool(getattr(args, "force", False)))
    except Exception as e:
        print(f"error: {e}")
        return 1
    print(f"KEV catalog mirrored: {data['count']} CVEs (no API key; refresh: suijin pull cve --force)")
    return 0


def run_creds(args) -> int:
    import getpass

    from suijin.modules.ops.lib import credential_vault as vault

    action = getattr(args, "creds_action", "list")

    def _pass(new: bool = False) -> str:
        if new:
            return getpass.getpass("New vault passphrase: ")
        return getpass.getpass("Vault passphrase: ")

    if action == "init":
        print(vault.init_vault(getpass.getpass("Set vault passphrase: ")))
        return 0
    if not vault.vault_exists():
        print("No vault — run: suijin creds init")
        return 1
    if action == "list":
        print(vault.list_credentials(_pass(), reveal=bool(getattr(args, "reveal", False))))
    elif action == "add":
        print(
            vault.add_credential(
                args.service,
                args.type or "password",
                args.value,
                username=args.username or "",
                notes=args.notes or "",
                passphrase=_pass(),
            )
        )
    elif action == "get":
        entries = [c for c in vault.load_vault(_pass()) if args.service in c.get("service", "")]
        for c in entries:
            print(f"{c.get('service')} | {c.get('type')} | {c.get('username', '')} | {c.get('value')}")
        if not entries:
            print(f"No credentials matching '{args.service}'.")
    elif action == "export":
        print(vault.export_credentials(_pass(), redact=not getattr(args, "plain", False)))
    return 0


def run_dossier(args) -> int:
    from suijin.modules.ops.lib.dossier import build_dossier, render_dossier

    try:
        print(render_dossier(build_dossier(args.target)))
    except ValueError as e:
        print(f"error: {e}")
        return 1
    return 0


def run_rules(args) -> int:
    from suijin.modules.ops.lib.governance import RULES_PATH, load_rules, validate_rules

    action = getattr(args, "rules_action", "validate")
    if action == "list":
        rules = load_rules()
        if not rules:
            print(f"No custom rules (create {RULES_PATH.name} — see docs for the schema)")
            return 0
        for r in rules:
            print(
                f"  {r.get('name', '?'):24} {r.get('field', 'body'):8} w{r.get('weight', 3)}  {r.get('pattern', '')[:50]}"
            )
        return 0
    problems = validate_rules()
    if not problems:
        n = len(load_rules())
        print(
            f"[ok] {RULES_PATH.name}: valid ({n} rule(s))"
            if RULES_PATH.exists()
            else "[--] no rules file (none needed)"
        )
        return 0
    for p in problems:
        print(f"[XX] {p}")
    return 1


def run_policy(args) -> int:
    from suijin.modules.ops.lib.governance import POLICY_PATH, load_policy, validate_policy

    action = getattr(args, "policy_action", "check")
    if action == "show":
        print(json.dumps(load_policy(), indent=2))
        return 0
    problems = validate_policy()
    if not POLICY_PATH.exists():
        print(f"[--] no policy file — defaults apply (private scopes only). Create {POLICY_PATH.name} to customize.")
        return 0
    if not problems:
        pol = load_policy()
        print(
            f"[ok] {POLICY_PATH.name}: valid — {len(pol['allowed_target_scopes'])} scopes, "
            f"{len(pol['blocked_tools'])} blocked tools, {len(pol['blocked_arg_patterns'])} arg patterns"
        )
        return 0
    for p in problems:
        print(f"[XX] {p}")
    return 1


def run_providers(args) -> int:
    from suijin.modules.providers.lib import generate
    from suijin.modules.redteam.lib.red.config_loader import load_config

    cfg = load_config()
    if getattr(args, "all", False):
        chain = ["deepseek", "zai", "gemini", "anthropic", "amd", "huggingface"]
    else:
        chain = [cfg.get("provider", "deepseek")] + (cfg.get("fallback_providers") or [])
    ok_count = 0
    for provider in dict.fromkeys(chain):
        env = {
            "zai": "ZAI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "amd": "AMD_API_KEY",
            "huggingface": "HF_TOKEN",
        }.get(provider, "")
        if env and not os.environ.get(env):
            print(f"  {provider:12} SKIP (no {env})")
            continue
        test_cfg = {**cfg, "provider": provider}
        t0 = time.time()
        try:
            out = generate(
                [{"role": "user", "content": "Reply with the single word: pong"}], test_cfg, max_tokens=8, retries=1
            )
        except Exception as e:
            out = f"Error: {e}"
        ms = int((time.time() - t0) * 1000)
        if str(out).startswith("Error:"):
            print(f"  {provider:12} FAIL ({ms} ms) — {str(out)[:90]}")
        else:
            ok_count += 1
            print(f"  {provider:12} ok ({ms} ms) — {str(out)[:40]!r}")
    print(f"\n{ok_count}/{len(set(chain))} provider(s) responding")
    return 0 if ok_count else 1


def run_module_manager(args) -> int:
    """`suijin module` — bare opens the Textual manager; verbs call the API."""
    from suijin.modules import manager as mgmt

    action = getattr(args, "module_action", "tui")
    try:
        if action == "tui":
            from suijin.modules.manager_tui import ModuleManager

            ModuleManager().run()
            return 0
        if action == "list":
            for e in mgmt.list_modules():
                mark = "●" if e["enabled"] else "○"
                print(f"  {mark} {e['tier']:12} {e['id']:20} v{e['version']}")
            return 0
        if action == "info":
            info = mgmt.module_info(args.id)
            print(f"{info['id']} v{info['version']} [{info['tier']}] enabled={info['enabled']}")
            print(f"  requires:    {', '.join(info['requires']) or '—'}")
            print(f"  permissions: {', '.join(info['permissions']) or '—'}")
            print(f"  source:      {info['source']}")
            return 0
        if action == "enable":
            print("enabled" if mgmt.set_enabled(args.id, True) else f"error: unknown module '{args.id}'")
            return 0 if mgmt.is_enabled(args.id) else 1
        if action == "disable":
            ok = mgmt.set_enabled(args.id, False)
            print("disabled (next boot)" if ok else f"error: unknown module '{args.id}'")
            return 0 if ok else 1
        if action == "install":
            print(mgmt.install(args.path, with_deps=bool(getattr(args, "with_deps", False))))
            return 0
        if action == "uninstall":
            mgmt.uninstall(args.id)
            print(f"{args.id}: uninstalled")
            return 0
    except mgmt.InstallError as e:
        print(f"error: {e}")
        return 1
    return 1


def run_module(args) -> int:
    from suijin.modules.tools.lib import module_sdk

    action = getattr(args, "module_action", "validate")
    if action == "init":
        try:
            mod_dir = module_sdk.scaffold_module(args.name)
        except (ValueError, FileExistsError) as e:
            print(f"error: {e}")
            return 1
        print(f"scaffolded {mod_dir.name}/ (manifest.json, main.py, skill.md)")
        print("implement main.py, then validate: suijin module validate " + mod_dir.name)
        return 0
    if action == "adopt":
        try:
            dest = module_sdk.adopt_addon(args.name)
        except (FileNotFoundError, FileExistsError, ValueError) as e:
            print(f"error: {e}")
            return 1
        print(f"adopted addon '{args.name}' -> {dest}")
        print("it now boots as a full pack (manifest, entry, skill); delete the addon folder when ready")
        return 0
    ok, problems = module_sdk.validate_module(args.name)
    if ok:
        print(f"[ok] module '{args.name}': manifest + implementation valid")
        return 0
    for p in problems:
        print(f"[XX] {p}")
    return 1


def run_skills_promote() -> int:
    """`suijin skills promote` — G47: critique tactics -> draft skills."""
    from suijin.modules.agent.lib.critique import promote_learnings

    print(promote_learnings(dry_run=True))
    print("\n(re-run programmatically with dry_run=False to write dormant _draft_*.md files)")
    return 0


def run_skills_decay() -> int:
    """`suijin skills decay` — G48: retirement candidates."""
    from suijin.modules.skills.entry import decay_report

    print(decay_report())
    return 0


def run_skills(args) -> int:
    from suijin.modules.tools.lib import self_improve as si

    action = getattr(args, "skills_action", "list")
    if action == "list":
        print(si.list_available_skills())
        return 0
    name = getattr(args, "name", "")
    if not name:
        print("error: skill name required (see: suijin skills)")
        return 1
    if action == "history":
        snaps = si.skill_history(name)
        if not snaps:
            print(f"No version history for '{name}' yet (snapshots are taken on every edit_skill).")
            return 0
        for s in snaps:
            print(f"  {s.name}  ({s.stat().st_size} bytes)")
        return 0
    if action == "diff":
        print(si.skill_diff(name, rev=getattr(args, "rev", None)))
        return 0
    if action == "rollback":
        print(si.skill_rollback(name, rev=getattr(args, "rev", None)))
        return 0
    return 1


def run_labs_campaign(args) -> int:
    from suijin.modules.ops.lib.housekeeping import render_campaign, run_campaign

    specs = []
    lab_dir = Path(_PKG_DIR) / "lab"
    for d in sorted(lab_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("__"):
            continue
        app = next((c for c in ("app.py", "vulnerable_app.py") if (d / c).exists()), None)
        if not app:
            continue
        port = _lab_port(d / app)
        if port:
            specs.append({"name": d.name, "port": int(port)})
    if not specs:
        print("No labs found.")
        return 1
    # boot each lab, probe, stop — sequential to keep ports clean
    import subprocess
    import sys
    import urllib.request

    results = {}
    for spec in specs:
        app = next(
            (
                lab_dir / spec["name"] / c
                for c in ("app.py", "vulnerable_app.py")
                if (lab_dir / spec["name"] / c).exists()
            )
        )
        proc = subprocess.Popen([sys.executable, str(app)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(15):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{spec['port']}/", timeout=1)
                    break
                except Exception:
                    time.sleep(0.3)
            single = run_campaign([spec], out_dir=_ad("reports"))
            results.update(single["labs"])
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    merged = {
        "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "labs": results,
        "summary": {
            "total": len(results),
            "reachable": sum(1 for v in results.values() if v["reachable"]),
            "flags_exposed": sum(len(v["flags"]) for v in results.values()),
        },
    }
    print(render_campaign(merged))
    print("\ncapability baseline — landing-page exposure only; exploitation is the agent's job.")
    return 0


def _enrich_traffic(entries: list) -> list:
    """Score raw lab log entries with the blue team's real anomaly detector
    (moved from the retired web UI; the watch verb tiers on it)."""
    try:
        from suijin.modules.blueteam.lib.blue.traffic.anomaly_detector import detect_anomalies

        for e in entries:
            try:
                signals = detect_anomalies(e, {"methods": {e.get("method", "GET"): 1}})
                e["ui_score"] = sum(sig[1] for sig in signals)
                e["ui_signals"] = [sig[0] for sig in signals]
            except Exception:
                e.setdefault("ui_score", 0)
                e.setdefault("ui_signals", [])
    except Exception:
        for e in entries:
            e.setdefault("ui_score", 0)
            e.setdefault("ui_signals", [])
    return entries


def run_gateway_cmd(args) -> int:
    """`suijin gateway` — serve the desktop API. Prints the session token ONCE."""
    from suijin.modules.console.lib.gateway import serve

    serve(
        host=getattr(args, "host", "127.0.0.1"),
        port=int(getattr(args, "port", 7331)),
        token=getattr(args, "token", None),
    )
    return 0


def run_tokens_cmd(args) -> int:
    """`suijin tokens` — the true token tally with source attribution."""
    from suijin.modules.providers.lib import get_usage

    u = get_usage()
    if not u.get("calls"):
        print("no LLM calls recorded this session")
        return 0
    api_n = u.get("api_reported_calls", 0)
    est_n = u.get("estimated_calls", 0)
    print(f"calls: {u['calls']}  ({api_n} API-reported, {est_n} client-estimated)")
    print(f"tokens: {u['input_tokens']:,} in + {u['output_tokens']:,} out = {u['input_tokens'] + u['output_tokens']:,}")
    print(
        f"cost:   ${u['est_cost_usd']:.4f}" + ("" if u.get("priced") else "  (fallback rates — some models unpriced)")
    )
    for prov, d in sorted(u.get("by_provider", {}).items()):
        print(f"  {prov:10} {d['calls']:>4} calls  {d['input']:,} in  {d['output']:,} out  ${d['cost_usd']:.4f}")
    if est_n:
        print(
            "note: estimated calls use a client-side approximation (the API omitted usage); treat their share as +/-20%"
        )
    return 0


def run_kg_graph() -> int:
    """`suijin kg graph` — G49: mermaid export of the knowledge graph."""
    from suijin.modules.redteam.lib.intel.knowledge_graph import export_mermaid

    print(export_mermaid())
    return 0


def run_market_cmd(args) -> int:
    """`suijin market search|install|update|list` — F41-F43."""
    from suijin.modules import marketplace as mp

    action = getattr(args, "action", "list")
    idx = getattr(args, "index", None) or mp.DEFAULT_INDEX
    try:
        if action == "search":
            hits = mp.search(getattr(args, "name", "") or "", idx)
            if not hits:
                print("no matches")
                return 0
            for h in hits[:20]:
                print(f"  {h['id']:24} v{h.get('version', '?'):8} {str(h.get('description', ''))[:60]}")
            return 0
        if action == "install":
            out = mp.install_pack(getattr(args, "name", ""), idx)
        elif action == "update":
            out = mp.update_pack(getattr(args, "name", ""), idx)
        else:
            installed = mp.list_installed()
            if not installed:
                print("no user-installed packs (~/.suijin/modules is empty)")
                return 0
            for i in installed:
                print(f"  {i['id']:24} v{i['version']:8} {i['path']}")
            return 0
        print(out)
        return 1 if out.startswith("Error") else 0
    except (OSError, ValueError) as e:
        print(f"error: {e}")
        return 1


def run_engage_cmd(args) -> int:
    """`suijin engage <template> <target>` — C21 template application."""
    from suijin.modules.ops.lib import engagement_templates as et

    if getattr(args, "list", False):
        print(et.list_templates())
        return 0
    try:
        resolved = et.apply_template(getattr(args, "template", ""), getattr(args, "target", ""))
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}")
        return 1
    print(json.dumps(resolved, indent=2))
    print("\nlaunch: suijin (config applied above; recipes: " + ", ".join(resolved.get("recipes", [])) + ")")
    return 0


def run_theater_cmd(args) -> int:
    """`suijin theater` — C26 animated session replay."""
    import json as _json

    from suijin.modules.platform.lib.workspace import artifact_dir
    from suijin.modules.tools.lib.theater import render_frames

    sdir = artifact_dir("sessions")
    sessions = sorted(sdir.glob("*.json")) if sdir.is_dir() else []
    if not sessions:
        print("no saved sessions")
        return 1
    data = _json.loads(sessions[-1].read_text())
    print(f"replaying: {data.get('objective', '?')[:60]}")
    for frame in render_frames(data.get("iterations") or []):
        print(frame)
    return 0


def run_wordlist_cmd(action: str, name: str) -> int:
    """`suijin wordlist list|fetch` — F45 hub."""
    from suijin.modules.knowledge.lib import wordlist_hub

    print(wordlist_hub.catalog() if action == "list" else wordlist_hub.fetch(name))
    return 0


def run_plan_cmd(args) -> int:
    """`suijin plan "<objective>"` — ordered subtask decomposition."""
    from suijin.modules.agent.lib.decompose import decompose, render

    cfg = {}
    try:
        with open(os.path.join(_PKG_DIR, "config.json")) as f:
            cfg = json.load(f)
    except OSError:
        pass

    def _gen(messages, config=None, **kw):
        from suijin.modules.providers.lib import generate

        return generate(messages, {**cfg, **(config or {})})

    print(render(decompose(getattr(args, "objective", ""), cfg, _gen)))
    return 0


def run_module_test(args) -> int:
    """`suijin module test <name>` — F44 pack harness."""
    from suijin.modules.tools.lib.module_sdk import test_pack

    ok, lines = test_pack(getattr(args, "name", ""))
    for ln in lines:
        print(ln)
    print(f"\n{'PASS' if ok else 'FAIL'}: {getattr(args, 'name', '?')}")
    return 0 if ok else 1


def run_recipes_cmd(args) -> int:
    """`suijin recipes [list|mine]` — macros + mined proposals."""
    from suijin.modules.tools.lib.recipes import mine_recipes, recipe_list

    action = getattr(args, "action", "list") or "list"
    if action == "mine":
        print(mine_recipes())
    else:
        print(recipe_list())
    return 0


def run_profile_cmd(args) -> int:
    """`suijin profile` — prompt budget profile of the newest saved session."""
    import json

    from suijin.modules.agent.lib.profiler import render
    from suijin.modules.platform.lib.workspace import artifact_dir

    sdir = artifact_dir("sessions")
    sessions = sorted(sdir.glob("*.json")) if sdir.is_dir() else []
    if not sessions:
        print("no saved sessions yet")
        return 1
    data = json.loads(sessions[-1].read_text())
    print(f"session: {data.get('objective', '?')[:60]} ({sessions[-1].name})")
    if not data.get("prompt_profile"):
        print("this session predates the profiler — run a new engagement")
        return 0
    pseudo_state = {
        "_prompt_profile": data.get("prompt_profile"),
        "_prompt_profile_trend": data.get("prompt_profile_trend", []),
    }
    print(render(pseudo_state))
    return 0


def run_spar_cmd(args) -> int:
    """`suijin spar` — detector practice volley, scored against a baseline."""
    from suijin.modules.ops.lib.sparring import render_spar, run_spar

    result, line = run_spar(
        name=getattr(args, "name", "default") or "default",
        save_baseline=bool(getattr(args, "save_baseline", False)),
        fail_on_regression=bool(getattr(args, "fail_on_regression", False)),
        threshold=int(getattr(args, "threshold", 5) or 5),
    )
    print(render_spar(result, line))
    return 1 if result.get("fail") else 0


def run_watch(args) -> int:
    import signal

    from suijin.modules.ops.lib.housekeeping import tail_file, watch_lines
    from suijin.modules.platform.lib.constants import BLUE_TRAFFIC_LOG

    path = Path(getattr(args, "traffic", None) or BLUE_TRAFFIC_LOG)
    if not path.exists():
        print(f"No traffic log at {path} — run the blue lab or pass --traffic <file>")
        return 1
    print(f"watching {path} (Ctrl+C to stop)")
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        # _enrich_traffic is list-based; watch_lines wants a per-entry fn
        per_entry = lambda e: _enrich_traffic([e])[0]  # noqa: E731
        for line in tail_file(path):
            for out in watch_lines([line], enrich=per_entry):
                print(out)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def run_timeline() -> int:
    from suijin.modules.ops.lib.housekeeping import build_timeline

    events = build_timeline()
    if not events:
        print("No engagement history yet (audits, sessions, reports all empty).")
        return 0
    last_day = None
    for e in events:
        day, clock = e["ts"].split(" ")
        if day != last_day:
            print(f"\n{day}")
            last_day = day
        print(f"  {clock}  {e['kind']:20} {e['detail'][:70]}")
    return 0


def run_clean(args) -> int:
    from suijin.modules.ops.lib.housekeeping import clean_workspace

    print(clean_workspace(apply=bool(getattr(args, "apply", False)), age_days=int(getattr(args, "days", 30))))
    return 0


def run_notify(args) -> int:
    from suijin.modules.ops.lib import notify

    action = getattr(args, "notify_action", "send")
    if action == "test":
        if not notify.load_config():
            print(notify.write_example_config())
            return 0
        for line in notify.send("suijin", "notification test — channel works"):
            print(f"  {line}")
        return 0
    if action == "send":
        msg = " ".join(getattr(args, "message", []) or [])
        if not msg:
            print("error: message required — suijin notify send 'engagement finished'")
            return 1
        for line in notify.send("suijin", msg):
            print(f"  {line}")
        return 0
    return 1


def run_scope(args) -> int:
    """`suijin scope` — Burp-style scope TUI (edits suijin/policy.json)."""
    import curses

    from suijin import tui_scope

    try:
        curses.wrapper(tui_scope.run)
        return 0
    except curses.error as e:
        print(f"error: scope TUI needs a real terminal — {e}")
        print("non-interactive: edit suijin/policy.json directly, or `suijin policy show`")
        return 1


def run_approvals(args) -> int:
    from suijin.modules.ops.lib import approvals as ap

    action = getattr(args, "approvals_action", "list")
    if action == "list":
        print(ap.render_list())
        return 0
    if action == "clear":
        print(ap.clear_session())
        return 0
    item_id = getattr(args, "id", None)
    if item_id is None:
        print("error: approval id required (see: suijin approvals list)")
        return 2
    if action == "approve":
        print(ap.decide(int(item_id), approve=True))
    elif action == "deny":
        print(ap.decide(int(item_id), approve=False))
    return 0


def run_panic(args) -> int:
    from suijin.modules.ops.lib.panic import panic

    print(panic(dry_run=bool(getattr(args, "dry_run", False))))
    return 0


def run_compliance(args) -> int:
    from suijin.modules.ops.lib.compliance import load_findings, map_findings, render

    findings = load_findings(getattr(args, "engagement", None))
    if not findings:
        print(
            "No findings recorded for this engagement (findings land in suijin_agent/audit_trails/ during engagements)."
        )
        return 0
    print(render(map_findings(findings)))
    return 0


def main(argv=None):
    import warnings

    from suijin.modules.platform.lib.config_models import CostCapWarning

    warnings.filterwarnings("ignore", category=CostCapWarning)  # one red line in engagements instead
    warnings.filterwarnings("ignore", message=".*allowed_objects.*")  # any category — langchain uses its own base class
    parser = argparse.ArgumentParser(
        prog="suijin",
        description="Suijin — autonomous red & blue teaming. "
        "Run bare to launch the TUI; subcommands are non-interactive.",
    )
    parser.add_argument("--version", action="version", version=f"suijin {_ver()}")
    sub = parser.add_subparsers(dest="command")

    # Simple offline verbs — every one is scriptable and exits 0 on success.
    SIMPLE_COMMANDS = {
        "doctor": ("verify the environment is ready", run_doctor),
        "selftest": ("offline smoke test — no network, no API keys", run_selftest),
        "status": ("one-page system status summary", run_status),
        "version": ("print version, python, and package details", run_version),
        "env": ("show API key presence (names only, never values)", run_env),
        "tools": ("list all agent tools with availability", run_tools_list),
        "modules": ("list loaded module packs", run_modules_list),
        "workspace": ("workspace layout, usage, and symlink health", run_workspace_status),
        "reports": ("list engagement reports", run_reports_list),
        "sessions": ("list saved engagement sessions", run_sessions_list),
        "timeline": ("unified engagement timeline across artifacts", run_timeline),
    }
    for name, (help_text, fn) in SIMPLE_COMMANDS.items():
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=lambda _a, _fn=fn: _fn())

    dossier = sub.add_parser("dossier", help="per-target intelligence dossier (KG + failures + history)")
    dossier.add_argument("target", help="IP / hostname / URL")
    dossier.set_defaults(func=run_dossier)

    clean = sub.add_parser("clean", help="workspace cleaner — dry-run by default")
    clean.add_argument("--apply", action="store_true", help="archive stale files then delete")
    clean.add_argument("--days", type=int, default=30, help="staleness threshold (default 30)")
    clean.set_defaults(func=run_clean)

    wl = sub.add_parser("wordlist", help="curated wordlist hub (fetch checksummed SecLists subsets)")
    wl_sub = wl.add_subparsers(dest="wl_action")
    wl_sub.add_parser("list", help="catalog + local state").set_defaults(func=lambda a: run_wordlist_cmd("list", ""))
    fetch_p = wl_sub.add_parser("fetch", help="fetch a curated wordlist")
    fetch_p.add_argument("name", help="catalog name")
    fetch_p.set_defaults(func=lambda a: run_wordlist_cmd("fetch", a.name))
    wl.set_defaults(func=lambda a: run_wordlist_cmd("list", ""))

    gateway_p = sub.add_parser("gateway", help="run the desktop-app gateway (typed API + live event stream)")
    gateway_p.add_argument(
        "--host", default="127.0.0.1", help="bind host (default localhost; remote = explicit opt-in)"
    )
    gateway_p.add_argument("--port", type=int, default=7331)
    gateway_p.add_argument("--token", default=None, help="fixed session token (sidecar mode); default: random per boot")
    gateway_p.set_defaults(func=run_gateway_cmd)

    tokens_p = sub.add_parser("tokens", help="exact token usage: API-reported vs estimated, per provider, cost")
    tokens_p.set_defaults(func=run_tokens_cmd)

    kggraph = sub.add_parser("kg", help="red knowledge graph: graph (mermaid) export")
    kggraph.add_argument("kg_action", nargs="?", default="graph", choices=["graph"], help="export the mermaid graph")
    kggraph.set_defaults(func=lambda _a: run_kg_graph())

    market = sub.add_parser("market", help="pack marketplace: search/install/update from index URLs")
    market.add_argument("action", choices=["search", "install", "update", "list"], help="marketplace action")
    market.add_argument("name", nargs="?", help="pack id (search term for 'search')")
    market.add_argument("--index", default=None, help="index URL (default: the community index)")
    market.set_defaults(func=run_market_cmd)

    engage = sub.add_parser("engage", help="apply an engagement template to a target")
    engage.add_argument("template", nargs="?", help="template name (suijin engage --list)")
    engage.add_argument("target", nargs="?", help="concrete target")
    engage.add_argument("--list", action="store_true", help="list templates")
    engage.set_defaults(func=run_engage_cmd)

    theater_p = sub.add_parser("theater", help="animated replay of the latest session")
    theater_p.set_defaults(func=run_theater_cmd)

    plan = sub.add_parser("plan", help="decompose an objective into an ordered subtask plan")
    plan.add_argument("objective", help="the engagement objective")
    plan.set_defaults(func=run_plan_cmd)

    recipes = sub.add_parser("recipes", help="tool recipes: list macros (or mine new ones from history)")
    recipes.add_argument("action", nargs="?", choices=["list", "mine"], default="list")
    recipes.set_defaults(func=run_recipes_cmd)

    profile = sub.add_parser("profile", help="prompt budget profile of the latest session (token breakdown + growth)")
    profile.set_defaults(func=run_profile_cmd)

    spar = sub.add_parser("spar", help="sparring mode: detector practice volley vs stored baseline")
    spar.add_argument("--name", default="default", help="baseline name (default 'default')")
    spar.add_argument("--save-baseline", action="store_true", help="store this run as the new baseline")
    spar.add_argument("--fail-on-regression", action="store_true", help="exit 1 if F1 drops below baseline")
    spar.add_argument("--threshold", type=int, default=5, help="detector threshold (default 5)")
    spar.set_defaults(func=run_spar_cmd)

    watch = sub.add_parser("watch", help="live-score a traffic log as it grows (Ctrl+C to stop)")
    watch.add_argument("--traffic", help="traffic .jsonl (default: the live blue log)")
    watch.set_defaults(func=run_watch)

    # skills: list (default) + history/diff/rollback
    skills = sub.add_parser("skills", help="agent-editable skills: list/history/diff/rollback")
    skills_sub = skills.add_subparsers(dest="skills_action")
    skills_sub.add_parser("list", help="list all skills").set_defaults(func=lambda _a: run_skills_list())
    skills_sub.add_parser("promote", help="draft critique tactics as skills for operator review").set_defaults(
        func=lambda _a: run_skills_promote()
    )
    skills_sub.add_parser("decay", help="flag drop-in skills no engagement ever referenced").set_defaults(
        func=lambda _a: run_skills_decay()
    )
    hist = skills_sub.add_parser("history", help="revision snapshots for a skill")
    hist.add_argument("name", help="skill name")
    hist.set_defaults(func=run_skills)
    diff = skills_sub.add_parser("diff", help="unified diff of a revision vs the live skill")
    diff.add_argument("name", help="skill name")
    diff.add_argument("--rev", help="revision substring (default: latest)")
    diff.set_defaults(func=run_skills)
    rb = skills_sub.add_parser("rollback", help="restore a skill from history")
    rb.add_argument("name", help="skill name")
    rb.add_argument("--rev", help="revision substring (default: latest)")
    rb.set_defaults(func=run_skills)
    skills.set_defaults(func=lambda _a: run_skills_list())

    # labs: list (default) + run campaign
    labs = sub.add_parser("labs", help="built-in labs: list / run campaign")
    labs_sub = labs.add_subparsers(dest="labs_action")
    labs_sub.add_parser("list", help="list labs with ports").set_defaults(func=lambda _a: run_labs_list())
    labs_sub.add_parser("run", help="boot + probe every lab; capability matrix").set_defaults(func=run_labs_campaign)
    labs.set_defaults(func=lambda _a: run_labs_list())

    export = sub.add_parser("export", help="chain-of-custody evidence bundle (zip + SHA-256 manifest)")
    export.add_argument("--out", help="output zip path (default suijin_agent/exports/<ts>.zip)")
    export.add_argument("--with-creds", action="store_true", help="include credentials.json (sensitive)")
    export.add_argument("--verify", metavar="ZIP", help="verify an existing bundle's hashes")
    export.set_defaults(func=run_export)

    debrief = sub.add_parser("debrief", help="engagement analytics from audit trails")
    debrief.add_argument("-v", "--verbose", action="store_true", help="per-engagement detail")
    debrief.set_defaults(func=run_debrief)

    replay = sub.add_parser("replay", help="step through an engagement timeline")
    replay.add_argument("--list", dest="list_replays", action="store_true", help="list replayable engagements")
    replay.add_argument("--file", help="audit trail file (interactive picker when omitted)")
    replay.add_argument("--export-md", metavar="OUT", help="write the full transcript to a markdown file")
    replay.set_defaults(func=run_replay)

    ev = sub.add_parser("eval", help="replay recorded traffic through the blue detector (offline)")
    ev.add_argument("--traffic", help="traffic .jsonl (default: the live blue log)")
    ev.add_argument("--labels", help='labels.jsonl with {"label":..., "any":[...]} rules')
    ev.add_argument("--threshold", type=int, default=5, help="score threshold to evaluate (default 5)")
    ev.add_argument("--no-sweep", action="store_true", help="skip the threshold sweep")
    ev.set_defaults(func=run_eval)

    battle = sub.add_parser("battle", help="red vs blue on the lab: --real drives the actual LLM agent")
    battle.add_argument("--port", type=int, default=5906, help="lab port (default 5906; --real/--mock use 5907)")
    battle.add_argument(
        "--real",
        action="store_true",
        help="REAL battle: the LLM red agent attacks the live lab; blue scores every request (uses your configured provider)",
    )
    battle.add_argument(
        "--mock", action="store_true", help="real battle pipeline with a scripted red (offline, deterministic)"
    )
    battle.add_argument("--objective", default="", help="override the red objective (--real)")
    battle.set_defaults(func=run_battle_cmd)

    # bench: graded lab benchmark (flags/tools/cost) with persisted history
    bench_p = sub.add_parser("bench", help="graded lab benchmark: agent vs lab, flags/tools/cost score")
    bench_p.add_argument("--lab", default="", help="one lab (log4shell, wordpress, oauth); default = all")
    bench_p.add_argument("--live", action="store_true", help="use the configured LLM provider (default: scripted mock)")
    bench_p.add_argument("--history", action="store_true", help="show past bench runs")
    bench_p.set_defaults(func=run_bench_cmd)

    # kb: read full docs + diff build vs cache
    kb = sub.add_parser("kb", help="knowledge base: read full docs / diff build")
    kb_sub = kb.add_subparsers(dest="kb_action")
    kb_read = kb_sub.add_parser("read", help="dump a full (untruncated) KB document")
    kb_read.add_argument("path", help="doc path or unique substring (e.g. _gtfobins/awk)")
    kb_read.add_argument("--source", help="restrict to one KB source")
    kb_read.set_defaults(func=run_kb_read)
    kb_sub.add_parser("diff", help="compare the built index against cached tarballs").set_defaults(func=run_kb_diff)
    kb.set_defaults(func=lambda _a: run_kb_diff())

    pull = sub.add_parser("pull", help="download resources (knowledge bases, ...)")
    pull_sub = pull.add_subparsers(dest="pull_target")
    pull_kb = pull_sub.add_parser("kb", help="download & compile security knowledge bases into suijin/kb.sqlite3")
    pull_kb.add_argument("--force", action="store_true", help="re-download even if a tarball is cached")
    pull_kb.add_argument("--sources", nargs="*", help="subset of sources to pull (default: all)")
    pull_kb.add_argument("--list", dest="list_sources", action="store_true", help="list available sources and exit")
    pull_kb.add_argument("--status", action="store_true", help="show what's indexed (offline) and exit")
    pull_kb.set_defaults(func=run_pull_kb)
    pull_cve = pull_sub.add_parser("cve", help="mirror the CISA KEV catalog (no API key)")
    pull_cve.add_argument("--force", action="store_true", help="refresh even if <24h old")
    pull_cve.add_argument("--status", action="store_true", help="show mirror status (offline)")
    pull_cve.set_defaults(func=run_pull_cve)

    creds = sub.add_parser("creds", help="credential vault (encrypted at rest)")
    creds_sub = creds.add_subparsers(dest="creds_action")
    creds_sub.add_parser("init", help="create the vault (imports legacy credentials.json)").set_defaults(func=run_creds)
    creds_list = creds_sub.add_parser("list", help="list credentials (values hidden)")
    creds_list.add_argument("--reveal", action="store_true", help="show plaintext values")
    creds_list.set_defaults(func=run_creds)
    creds_add = creds_sub.add_parser("add", help="store a credential")
    creds_add.add_argument("--service", required=True)
    creds_add.add_argument("--value", required=True)
    creds_add.add_argument("--type", default="password")
    creds_add.add_argument("--username", default="")
    creds_add.add_argument("--notes", default="")
    creds_add.set_defaults(func=run_creds)
    creds_get = creds_sub.add_parser("get", help="search credentials (revealed)")
    creds_get.add_argument("service", help="service substring")
    creds_get.set_defaults(func=run_creds)
    creds_export = creds_sub.add_parser("export", help="export (redacted by default)")
    creds_export.add_argument("--plain", action="store_true", help="export PLAINTEXT (careful)")
    creds_export.set_defaults(func=run_creds)
    creds.set_defaults(func=lambda _a: run_creds(argparse.Namespace(creds_action="list")))

    rules = sub.add_parser("rules", help="custom detector rules: validate / list")
    rules_sub = rules.add_subparsers(dest="rules_action")
    rules_sub.add_parser("validate", help="lint detector_rules.json").set_defaults(func=run_rules)
    rules_sub.add_parser("list", help="list custom rules").set_defaults(func=run_rules)
    rules.set_defaults(func=lambda _a: run_rules(argparse.Namespace(rules_action="validate")))

    policy = sub.add_parser("policy", help="engagement policy: check / show")
    policy_sub = policy.add_subparsers(dest="policy_action")
    policy_sub.add_parser("check", help="lint policy.json").set_defaults(func=run_policy)
    policy_sub.add_parser("show", help="effective policy").set_defaults(func=run_policy)
    policy.set_defaults(func=lambda _a: run_policy(argparse.Namespace(policy_action="check")))

    providers_cmd = sub.add_parser("providers", help="probe configured providers (tiny live request)")
    providers_cmd.add_argument("--all", action="store_true", help="probe every provider with a key, not just the chain")
    providers_cmd.set_defaults(func=run_providers)

    module = sub.add_parser("module", help="module manager (bare = TUI) + SDK")
    module_sub = module.add_subparsers(dest="module_action")
    module_sub.add_parser("tui", help="open the Textual Module Manager").set_defaults(func=run_module_manager)
    m_list = module_sub.add_parser("list", help="list modules with tiers + state")
    m_list.set_defaults(func=run_module_manager)
    m_info = module_sub.add_parser("info", help="module details")
    m_info.add_argument("id", help="module id")
    m_info.set_defaults(func=run_module_manager)
    m_en = module_sub.add_parser("enable", help="enable a module (next boot)")
    m_en.add_argument("id", help="module id")
    m_en.set_defaults(func=run_module_manager)
    m_dis = module_sub.add_parser("disable", help="disable a module (next boot)")
    m_dis.add_argument("id", help="module id")
    m_dis.set_defaults(func=run_module_manager)
    m_inst = module_sub.add_parser("install", help="install a module from a path")
    m_inst.add_argument("path", help="module directory containing plugin.json")
    m_inst.add_argument(
        "--with-deps", action="store_true", help="also pip-install declared python deps (explicit opt-in)"
    )
    m_inst.set_defaults(func=run_module_manager)
    m_un = module_sub.add_parser("uninstall", help="remove an installed (user-space) module")
    m_un.add_argument("id", help="module id")
    m_un.set_defaults(func=run_module_manager)
    mod_init = module_sub.add_parser("init", help="scaffold a new module pack (SDK)")
    mod_test = module_sub.add_parser("test", help="test a pack end-to-end (author's pre-publish gate)")
    mod_test.add_argument("name", help="pack directory name")
    mod_test.set_defaults(func=lambda a: run_module_test(a))

    mod_adopt = module_sub.add_parser("adopt", help="graduate an addon (suijin/addons/<name>) into a full pack")
    mod_adopt.add_argument("name", help="addon folder name under suijin/addons/")
    mod_init.add_argument("name", help="module name (snake_case)")
    mod_init.set_defaults(func=run_module)
    mod_val = module_sub.add_parser("validate", help="validate a module pack (SDK)")
    mod_val.add_argument("name", help="module directory name under ~/.suijin/modules")
    mod_val.set_defaults(func=run_module)
    module.set_defaults(func=run_module_manager)

    notify = sub.add_parser("notify", help="operator notifications: send / test")
    notify_sub = notify.add_subparsers(dest="notify_action")
    notify_send = notify_sub.add_parser("send", help="send a notification")
    notify_send.add_argument("message", nargs="+", help="message text")
    notify_send.set_defaults(func=run_notify)
    notify_sub.add_parser("test", help="write example config / test channels").set_defaults(func=run_notify)
    notify.set_defaults(func=run_notify)

    compliance = sub.add_parser("compliance", help="map engagement findings to CWE / OWASP / ATT&CK")
    compliance.add_argument("engagement", nargs="?", default=None, help="engagement name (default: newest audit trail)")
    compliance.set_defaults(func=run_compliance)

    approvals = sub.add_parser("approvals", help="HITL approval console: list/approve/deny/clear")
    approvals_sub = approvals.add_subparsers(dest="approvals_action")
    approvals_sub.add_parser("list", help="list approval requests").set_defaults(func=run_approvals)
    a_ok = approvals_sub.add_parser("approve", help="allow a tool for this session")
    a_ok.add_argument("id", type=int, help="approval request id")
    a_ok.set_defaults(func=run_approvals)
    a_no = approvals_sub.add_parser("deny", help="hard-block a tool for this session")
    a_no.add_argument("id", type=int, help="approval request id")
    a_no.set_defaults(func=run_approvals)
    approvals_sub.add_parser("clear", help="reset session verdicts (keep the log)").set_defaults(func=run_approvals)
    approvals.set_defaults(func=lambda _a: run_approvals(argparse.Namespace(approvals_action="list")))

    panic_cmd = sub.add_parser("panic", help="stop all Suijin processes + clear live state NOW")
    panic_cmd.add_argument("--dry-run", action="store_true", help="report what would happen")
    panic_cmd.set_defaults(func=run_panic)

    scope_cmd = sub.add_parser("scope", help="Burp-style target scope TUI (include/exclude/subdomains)")
    scope_cmd.set_defaults(func=run_scope)

    config = sub.add_parser("config", help="inspect and validate configuration")
    config_sub = config.add_subparsers(dest="config_action")
    config_show = config_sub.add_parser("show", help="effective config with secrets redacted")
    config_show.set_defaults(func=lambda _a: run_config_show())
    config_validate = config_sub.add_parser("validate", help="Pydantic-validate config.json + blue_config.json")
    config_validate.set_defaults(func=lambda _a: run_config_validate())

    args = parser.parse_args(argv)

    if args.command is None:
        # Default: launch the classic Rich TUI
        from suijin.main import main as tui_main

        try:
            tui_main()
        except KeyboardInterrupt:
            # Ctrl+C anywhere in the TUI: quiet exit (130 = SIGINT),
            # never a traceback
            print("\ncancelled")
            sys.exit(130)
        return

    if getattr(args, "func", None) is None:
        # `suijin pull` / `suijin config` with no action — show help.
        sub.choices[args.command].print_help()
        sys.exit(2)

    _audit_cli_call(args)
    sys.exit(args.func(args))


def _audit_cli_call(args):
    """One audit line per CLI verb invocation (never raises)."""
    try:
        from suijin.kernel.audit import ToolAudit
        from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

        verb = getattr(args, "command", "") or "?"
        tool_args = {
            k: v
            for k, v in vars(args).items()
            if k not in ("func", "command") and isinstance(v, (str, int, bool, float))
        }
        ToolAudit(WORKSPACE_DIR / "outputs" / "audit_trails", "cli_calls.jsonl", flush_every=1).record(
            surface="cli", name=verb, args=tool_args, outcome="invoked"
        )
    except Exception:  # noqa: BLE001 — audit must never break the CLI
        pass


if __name__ == "__main__":
    main()
