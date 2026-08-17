"""Medusa command-line entry point.

`medusa`      -> launch the classic Rich TUI
`medusa doctor` -> verify the environment is ready to hack
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
            if has_key:
                rows.append(_ok("config", f"provider={provider}, api key set"))
            else:
                rows.append(_warn("config", f"provider={provider}, no api key (heuristic mode works)"))
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
    parser = argparse.ArgumentParser(prog="medusa", description="Medusa — autonomous red & blue teaming")
    parser.add_argument("--version", action="version", version=f"medusa {VERSION}")
    sub = parser.add_subparsers(dest="command")

    doctor = sub.add_parser("doctor", help="verify the environment is ready")
    doctor.set_defaults(func=lambda _a: run_doctor())

    pull = sub.add_parser("pull", help="download resources (knowledge bases, ...)")
    pull_sub = pull.add_subparsers(dest="pull_target")
    pull_kb = pull_sub.add_parser("kb", help="download & compile security knowledge bases into medusa/kb.sqlite3")
    pull_kb.add_argument("--force", action="store_true", help="re-download even if a tarball is cached")
    pull_kb.add_argument("--sources", nargs="*", help="subset of sources to pull (default: all)")
    pull_kb.add_argument("--list", dest="list_sources", action="store_true", help="list available sources and exit")
    pull_kb.add_argument("--status", action="store_true", help="show what's indexed (offline) and exit")
    pull_kb.set_defaults(func=run_pull_kb)

    selftest = sub.add_parser("selftest", help="offline smoke test — no network, no API keys")
    selftest.set_defaults(func=lambda _a: run_selftest())

    args = parser.parse_args(argv)

    if args.command == "doctor":
        sys.exit(run_doctor())

    if args.command == "selftest":
        sys.exit(run_selftest())

    if args.command == "pull":
        if getattr(args, "func", None) is None:
            pull.print_help()
            sys.exit(2)
        sys.exit(args.func(args))

    # Default: launch the classic Rich TUI
    from medusa.main import main as tui_main

    tui_main()


if __name__ == "__main__":
    main()
