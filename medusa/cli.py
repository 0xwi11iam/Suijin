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

VERSION = "2.4.0"

REQUIRED_BINARIES = ["nmap", "gobuster", "feroxbuster", "john", "curl"]
OPTIONAL_BINARIES = [
    "sqlmap", "hydra", "amass", "subfinder", "httpx", "nuclei",
    "katana", "nikto", "whatweb", "sslscan", "ffuf", "msfconsole",
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

    # Config
    cfg = os.path.join(_pkg_parent, "config.json")
    if os.path.exists(cfg):
        try:
            import json
            data = json.load(open(cfg))
            has_key = bool(data.get("api_key")) or bool(os.environ.get("DEEPSEEK_API_KEY")) or bool(os.environ.get("OPENAI_API_KEY"))
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


def main(argv=None):
    parser = argparse.ArgumentParser(prog="medusa", description="Medusa — autonomous red & blue teaming")
    parser.add_argument("--version", action="version", version=f"medusa {VERSION}")
    sub = parser.add_subparsers(dest="command")

    doctor = sub.add_parser("doctor", help="verify the environment is ready")
    doctor.set_defaults(func=lambda _a: run_doctor())

    args = parser.parse_args(argv)

    if args.command == "doctor":
        sys.exit(run_doctor())

    # Default: launch the classic Rich TUI
    from medusa.main import main as tui_main
    tui_main()


if __name__ == "__main__":
    main()
