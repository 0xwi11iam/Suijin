"""Medusa command-line entry point.

`medusa`                 -> launch the classic Rich TUI
`medusa doctor`          -> verify the environment is ready
`medusa status`          -> one-page system summary
`medusa selftest`        -> offline smoke test (no network, no keys)
`medusa version`         -> release / python / package details
`medusa env`             -> API key presence (names only)
`medusa tools`           -> all agent tools + availability
`medusa modules`         -> loaded module packs
`medusa skills`          -> agent-editable skills
`medusa config show`     -> effective config (secrets redacted)
`medusa config validate` -> Pydantic validation of both configs
`medusa workspace`       -> workspace layout + usage + symlink health
`medusa reports`         -> engagement reports
`medusa sessions`        -> saved sessions
`medusa labs`            -> built-in vulnerable labs with ports
`medusa pull kb ...`     -> build/inspect the offline knowledge base

Every subcommand is non-interactive and scriptable (exit 0 = healthy).
"""

import argparse
import importlib
import os
import shutil
import socket
import sys

# Make the repo root importable so `from medusa import ...` works regardless of
# where this script lives (source checkout or installed into ~/.medusa/repo).
_pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _pkg_parent not in sys.path:
    sys.path.insert(0, _pkg_parent)

# Single source of truth: medusa/version.json (via the package __init__).
from medusa import __version__ as VERSION

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))

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

    # Config — lives inside the medusa/ package dir, not the repo root.
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
        from medusa.modules.loader import discover_modules, get_module_tools

        discover_modules()
        n = len(get_module_tools())
        rows.append(_ok("modules", f"{n} module tools loaded"))
    except Exception as e:
        rows.append(_fail("modules", str(e)))
        critical += 1

    # Knowledge base (optional — built on demand via `medusa pull kb`)
    try:
        from medusa.kb import kb_status

        st = kb_status()
        if st:
            per = ", ".join(f"{k}:{v:,}" for k, v in sorted(st.get("per_source", {}).items()))
            detail = f"{st['docs']:,} docs / {st['sources']} sources (built {st['built_at'][:10]})"
            if per:
                detail += f" [{per}]"
            if st.get("age_days") is not None and st["age_days"] > 30:
                detail += f" — STALE ({st['age_days']}d old, rerun: medusa pull kb --force)"
                rows.append(_warn("knowledge base", detail))
            else:
                rows.append(_ok("knowledge base", detail))
        else:
            rows.append(_warn("knowledge base", "not built — run: medusa pull kb"))
    except Exception as e:
        rows.append(_warn("knowledge base", str(e)))

    # Workspace layout (canonical root dir + inner symlink)
    try:
        import medusa.tools.workspace as ws

        ws.ensure_workspace_layout()
        inner = ws.PROJECT_DIR / "medusa" / "medusa_agent"
        if inner.is_symlink():
            rows.append(_ok("workspace", f"{ws.WORKSPACE_DIR} (symlink ok)"))
        else:
            rows.append(_warn("workspace", "medusa/medusa_agent is not a symlink -> ../medusa_agent"))
    except Exception as e:
        rows.append(_warn("workspace", str(e)))

    # Print
    print("Medusa doctor v" + VERSION)
    print("-" * 56)
    for status, name, detail in rows:
        mark = {"PASS": "ok", "WARN": "!!", "FAIL": "XX"}[status]
        print(f"  [{mark}] {name:14} {detail}")
    print("-" * 56)
    if critical:
        print(f"\n{critical} critical problem(s). Fix them and re-run 'medusa doctor'.")
        return 1
    print("\nReady. Run 'medusa' to start the interface.")
    return 0


def _has_any_api_key(pkg_dir: str) -> bool:
    """True if any supported provider key is set (env var or medusa/.env).

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
    """`medusa pull kb` — download & compile the security knowledge base."""
    from medusa.kb import DB_PATH, SOURCES, compile_kb, kb_status

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
            print("Enable them with: medusa pull kb")
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
            print(f"    {name:12} FAILED at last build — retry: medusa pull kb --sources {name}")
        if st.get("age_days") is not None and st["age_days"] > 30:
            print(f"  stale: built {st['age_days']} days ago — refresh with: medusa pull kb --force")
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
        print("Re-run later: medusa pull kb --sources " + " ".join(failed))
        print("Knowledge base PARTIALLY ENABLED — failed sources above are not searchable.")
    else:
        print("Knowledge base ENABLED — search_kb is now available to the agent.")
    return 0


# ── Non-interactive info commands ─────────────────────────────────────
# All offline, all safe to script: `medusa status && medusa labs` etc.

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
    print(f"medusa {VERSION}" + (f'  "{codename}"' if codename else "") + (f"  ({release})" if release else ""))
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
            where = "medusa/.env"
        else:
            where = ""
        print(f"  {name:20} {'SET' if where else 'not set':8}" + (f"({where})" if where else ""))
    return 0


def run_status() -> int:
    """One-page system summary: provider, KB, workspace, modules, lab port."""
    import json

    print(f"medusa {VERSION}")
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
        from medusa.kb import kb_status

        st = kb_status()
        if st:
            age = f", {st['age_days']}d old" if st.get("age_days") is not None else ""
            print(f"knowledge base:   {st['docs']:,} docs / {st['sources']} sources{age}")
        else:
            print("knowledge base:   NOT built — run: medusa pull kb")
    except Exception as e:
        print(f"knowledge base:   {e}")

    try:
        import medusa.tools.workspace as ws

        ws.ensure_workspace_layout()
        inner = ws.PROJECT_DIR / "medusa" / "medusa_agent"
        print(f"workspace:        {ws.WORKSPACE_DIR}" + ("" if inner.is_symlink() else "  (!! symlink missing)"))
    except Exception as e:
        print(f"workspace:        {e}")

    try:
        from medusa.modules.loader import discover_modules, get_module_tools

        discover_modules()
        print(f"modules:          {len(get_module_tools())} module tools loaded")
    except Exception as e:
        print(f"modules:          load failed — {e}")

    print(f"lab port:         5906 {'free' if _port_free(5906) else 'IN USE'}")
    return 0


def run_tools_list() -> int:
    """Every callable agent tool, core + module, with availability marks."""
    from medusa.tools.dispatch import list_route_tools

    core = sorted(list_route_tools())
    print(f"Core tools ({len(core)}):")
    for t in core:
        print(f"  {t}")
    try:
        from medusa.modules.loader import discover_modules, get_loaded_modules
        from medusa.tools.availability import missing_binaries

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
    from medusa.modules.loader import discover_modules, get_loaded_modules

    discover_modules()
    mods = get_loaded_modules() or {}
    if not mods:
        print("No module packs found (expected under Modules/Tools and Modules/Mods).")
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
    from medusa.tools.self_improve import list_available_skills

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
    from medusa.core.red.config_loader import load_config

    return load_config()


def run_config_show() -> int:
    """Effective red-team config (defaults merged), secrets redacted."""
    import json

    print(json.dumps(_redact(_effective_config()), indent=2, sort_keys=True))
    return 0


def run_config_validate() -> int:
    """Pydantic-validate config.json and blue_config.json. Exit 1 on failure."""
    import json

    from medusa.core.config_models import BlueConfig, RedConfig

    ok = True
    checks = (
        ("config.json", RedConfig),
        ("blue_config.json", BlueConfig),
    )
    for fname, model in checks:
        path = os.path.join(_PKG_DIR, fname)
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

    import medusa.tools.workspace as ws

    ws.ensure_workspace_layout()
    inner = ws.PROJECT_DIR / "medusa" / "medusa_agent"
    print(f"workspace: {ws.WORKSPACE_DIR}")
    print(
        f"symlink:   medusa/medusa_agent -> "
        f"{'../medusa_agent (ok)' if inner.is_symlink() else 'MISSING — run: medusa selftest'}"
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
    """Engagement reports in medusa_agent/reports (newest first, top 30)."""
    from datetime import datetime

    from medusa.tools.workspace import WORKSPACE_DIR

    rdir = WORKSPACE_DIR / "reports"
    files = []
    if rdir.exists():
        files = sorted((f for f in rdir.rglob("*") if f.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
    if not files:
        print("No reports yet — they land in medusa_agent/reports/ after an engagement.")
        return 0
    print(f"Reports in {rdir} (newest first):")
    for f in files:
        st = f.stat()
        when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {when}  {st.st_size / 1024:>8.0f} KB  {f.relative_to(rdir)}")
    return 0


def run_sessions_list() -> int:
    """Saved engagement sessions in medusa_agent/sessions (newest first)."""
    import json
    from datetime import datetime

    from medusa.tools.workspace import WORKSPACE_DIR

    sdir = WORKSPACE_DIR / "sessions"
    files = []
    if sdir.exists():
        files = sorted(sdir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        print("No saved sessions — medusa_agent/sessions/ fills up during engagements.")
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


def run_ui(args) -> int:
    """`medusa ui` — local-first web dashboard (127.0.0.1 only)."""
    from medusa.ui.server import run_server

    return run_server(port=int(getattr(args, "port", 0) or 7800),
                      open_browser=not getattr(args, "no_open", False))


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
        print(f"  {name:18} :{port:<5} python3 medusa/lab/{name}/{app}{suffix}")
        doc = _first_docstring(os.path.join(d, app))
        if doc:
            print(f"  {'':18} {doc}")
    if not found:
        print("No labs found under medusa/lab/.")
        return 1
    print("\nStart one, then point Red Team at http://127.0.0.1:<port>.")
    return 0


def run_selftest() -> int:
    """Offline smoke test: imports, KB gating, workspace anchors, sandbox.

    No network, no API keys, no side effects beyond workspace layout repair
    (which runs on every import of medusa.tools.runtime anyway).
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
        import medusa.kb  # noqa: F401
        import medusa.tools.dispatch  # noqa: F401 — pulls runtime, workspace, kb

        return "ok"

    def _kb_status():
        from medusa.kb import kb_status

        st = kb_status()
        if st:
            return f"built — {st['docs']:,} docs / {st['sources']} sources, search_kb ENABLED"
        return "not built — search_kb DISABLED (run: medusa pull kb)"

    def _catalog_gating():
        import medusa.kb as kb_mod
        from medusa.kb import DB_PATH as real_db
        from medusa.tools import dispatch

        built = dispatch.get_tool_catalog()
        with patch.object(kb_mod, "DB_PATH", real_db.parent / "_selftest_missing_.sqlite3"):
            missing = dispatch.get_tool_catalog()
        st = kb_mod.kb_status()
        if st:
            assert "**search_kb**" in built, "catalog must advertise search_kb when built"
        else:
            assert "DISABLED" in built, "catalog must list search_kb as DISABLED when not built"
        assert "DISABLED" in missing and "medusa pull kb" in missing
        return "catalog gating consistent (built + disabled states)"

    def _workspace_anchor():
        from medusa.tools.workspace import PROJECT_DIR, WORKSPACE_DIR, ensure_workspace_layout

        assert WORKSPACE_DIR == PROJECT_DIR / "medusa_agent"
        ensure_workspace_layout()  # repair if needed, then verify
        inner = PROJECT_DIR / "medusa" / "medusa_agent"
        assert inner.is_symlink() or not inner.exists(), (
            f"medusa/medusa_agent must be a symlink to ../medusa_agent (got {inner})"
        )
        return f"{WORKSPACE_DIR} (medusa/medusa_agent -> ../medusa_agent)"

    def _sandbox():
        from pathlib import Path

        from medusa.infra import job_runner
        from medusa.tools.workspace import WORKSPACE_DIR

        wd = Path(job_runner.get_sandbox_workdir())
        assert str(wd).startswith(str(WORKSPACE_DIR)), f"sandbox escaped workspace: {wd}"
        assert not job_runner.is_command_allowed("shutdown -h now")
        assert job_runner.is_command_allowed("nmap -sV 127.0.0.1")
        return "sandbox inside workspace, guardrails block system commands"

    def _boundary():
        from medusa.tools.workspace import resolve_workspace_path

        try:
            resolve_workspace_path("/etc/passwd")
        except PermissionError:
            return "writes confined to medusa_agent/ + allowlist"
        raise AssertionError("absolute path outside workspace was not rejected")

    def _modules():
        from medusa.modules.loader import discover_modules, get_module_tools

        discover_modules()
        return f"{len(get_module_tools())} module tools loaded"

    check("core imports", _imports)
    check("kb status", _kb_status)
    check("kb gating", _catalog_gating)
    check("workspace", _workspace_anchor)
    check("sandbox", _sandbox)
    check("boundary", _boundary)
    check("modules", _modules)

    print("Medusa selftest v" + VERSION)
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


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="medusa",
        description="Medusa — autonomous red & blue teaming. "
        "Run bare to launch the TUI; subcommands are non-interactive.",
    )
    parser.add_argument("--version", action="version", version=f"medusa {VERSION}")
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
        "skills": ("list agent-editable attack skills", run_skills_list),
        "workspace": ("workspace layout, usage, and symlink health", run_workspace_status),
        "reports": ("list engagement reports", run_reports_list),
        "sessions": ("list saved engagement sessions", run_sessions_list),
        "labs": ("list built-in vulnerable labs with ports", run_labs_list),
    }
    for name, (help_text, fn) in SIMPLE_COMMANDS.items():
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=lambda _a, _fn=fn: _fn())

    ui = sub.add_parser("ui", help="launch the local web dashboard (127.0.0.1)")
    ui.add_argument("--port", type=int, default=7800, help="listen port (default 7800)")
    ui.add_argument("--no-open", action="store_true", help="do not auto-open the browser")
    ui.set_defaults(func=run_ui)

    pull = sub.add_parser("pull", help="download resources (knowledge bases, ...)")
    pull_sub = pull.add_subparsers(dest="pull_target")
    pull_kb = pull_sub.add_parser("kb", help="download & compile security knowledge bases into medusa/kb.sqlite3")
    pull_kb.add_argument("--force", action="store_true", help="re-download even if a tarball is cached")
    pull_kb.add_argument("--sources", nargs="*", help="subset of sources to pull (default: all)")
    pull_kb.add_argument("--list", dest="list_sources", action="store_true", help="list available sources and exit")
    pull_kb.add_argument("--status", action="store_true", help="show what's indexed (offline) and exit")
    pull_kb.set_defaults(func=run_pull_kb)

    config = sub.add_parser("config", help="inspect and validate configuration")
    config_sub = config.add_subparsers(dest="config_action")
    config_show = config_sub.add_parser("show", help="effective config with secrets redacted")
    config_show.set_defaults(func=lambda _a: run_config_show())
    config_validate = config_sub.add_parser("validate", help="Pydantic-validate config.json + blue_config.json")
    config_validate.set_defaults(func=lambda _a: run_config_validate())

    args = parser.parse_args(argv)

    if args.command is None:
        # Default: launch the classic Rich TUI
        from medusa.main import main as tui_main

        tui_main()
        return

    if getattr(args, "func", None) is None:
        # `medusa pull` / `medusa config` with no action — show help.
        sub.choices[args.command].print_help()
        sys.exit(2)

    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
