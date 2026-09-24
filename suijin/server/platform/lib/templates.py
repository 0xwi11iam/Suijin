"""
suijin/core/templates.py — Engagement templates, config validation, health check.

Provides:
- Engagement templates (save/load target profiles)
- Config validation on startup
- Health check (API keys, tool availability, lab status)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = BASE_DIR / "templates"
TEMPLATES_DIR.mkdir(exist_ok=True)

# ── Default engagement templates ──────────────────────────────────────────────

DEFAULT_TEMPLATES = {
    "standard_web_app": {
        "name": "Standard Web Application Test",
        "description": "Full-scope web application penetration test",
        "ports": [80, 443, 8080, 8443],
        "wordlists": ["common.txt", "api-endpoints.txt"],
        "tools": ["nmap", "gobuster", "ffuf", "nikto", "sqlmap", "whatweb"],
        "checks": ["sqli", "xss", "csrf", "idor", "file_upload", "jwt", "cors"],
        "max_iterations": 50,
        "headless": False,
    },
    "quick_recon": {
        "name": "Quick Reconnaissance",
        "description": "Fast recon pass — ports, tech stack, endpoints only",
        "ports": [80, 443],
        "wordlists": ["quick.txt"],
        "tools": ["nmap", "whatweb", "gobuster", "httpx"],
        "checks": [],
        "max_iterations": 15,
        "headless": False,
    },
    "api_test": {
        "name": "API Security Test",
        "description": "REST + GraphQL API focused testing",
        "ports": [80, 443, 3000, 5000, 8000, 9000],
        "wordlists": ["api-endpoints.txt"],
        "tools": ["nmap", "ffuf", "sqlmap", "jwt_tool"],
        "checks": ["graphql", "jwt", "mass_assignment", "cors", "rate_limit"],
        "max_iterations": 40,
        "headless": False,
    },
    "js_spa": {
        "name": "JavaScript SPA Test",
        "description": "React/Remix/Next.js/Vue SPA — use browser MCP",
        "ports": [80, 443, 3000],
        "wordlists": ["api-endpoints.txt"],
        "tools": ["nmap", "whatweb", "mcp_browser_goto"],
        "checks": ["xss", "jwt", "csrf", "cors", "information_disclosure"],
        "max_iterations": 50,
        "headless": True,
    },
    "cloud_review": {
        "name": "Cloud Security Review",
        "description": "AWS/Azure/GCP misconfiguration testing",
        "ports": [80, 443],
        "wordlists": [],
        "tools": ["nmap", "sslscan", "search_cve"],
        "checks": ["ssrf", "information_disclosure", "subdomain_takeover"],
        "max_iterations": 30,
        "headless": False,
    },
}


def list_templates() -> list:
    """List all available engagement templates."""
    builtin = list(DEFAULT_TEMPLATES.keys())
    custom = [f.stem for f in TEMPLATES_DIR.glob("*.json") if f.stem not in builtin]
    return builtin + custom


def load_template(name: str) -> dict:
    """Load an engagement template by name."""
    if name in DEFAULT_TEMPLATES:
        return dict(DEFAULT_TEMPLATES[name])
    path = TEMPLATES_DIR / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text())
    return DEFAULT_TEMPLATES["standard_web_app"]


def save_template(name: str, config: dict) -> str:
    """Save a custom engagement template."""
    path = TEMPLATES_DIR / f"{name}.json"
    config["saved_at"] = datetime.now().isoformat()
    path.write_text(json.dumps(config, indent=2))
    return str(path)


# ── Health check ──────────────────────────────────────────────────────────────


def run_health_check() -> dict:
    """Run a comprehensive health check. Returns status dict."""
    results = {
        "timestamp": datetime.now().isoformat(),
        "checks": {},
        "all_ok": True,
    }

    # 1. Python version
    py_ok = sys.version_info >= (3, 10)
    results["checks"]["python_version"] = {
        "ok": py_ok,
        "detail": f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }

    # 2. API key
    config_path = BASE_DIR / "config.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    provider = config.get("provider", "deepseek")
    key_var = f"{provider.upper()}_API_KEY"
    has_key = bool(os.environ.get(key_var) or os.environ.get("HF_TOKEN"))
    results["checks"]["api_key"] = {"ok": has_key, "detail": f"Provider: {provider}, Key set: {has_key}"}

    # 3. .env file
    env_path = BASE_DIR / ".env"
    env_ok = env_path.exists() and env_path.stat().st_size > 0
    results["checks"]["env_file"] = {"ok": env_ok, "detail": str(env_path)}

    # 4. Tool availability (quick check for key tools)
    tools_to_check = ["nmap", "curl", "python3"]
    for tool in tools_to_check:
        import shutil

        found = shutil.which(tool) is not None
        results["checks"][f"tool_{tool}"] = {"ok": found, "detail": shutil.which(tool) or "not found"}

    # 5. Playwright
    import importlib.util

    if importlib.util.find_spec("playwright") is not None:
        results["checks"]["playwright"] = {"ok": True, "detail": "installed"}
    else:
        results["checks"]["playwright"] = {
            "ok": False,
            "detail": "not installed — run: pip install playwright && playwright install chromium",
        }

    # 6. Lab status (check if lab ports are already in use)
    import socket

    lab_ports = {
        5700: "DevOps Dashboard",
        5800: "CloudBoard Next (main)",
        5801: "CloudBoard Next (internal)",
        5802: "CloudBoard Next (legacy)",
    }
    for port, name in lab_ports.items():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        results["checks"][f"lab_{name}"] = {
            "ok": result != 0,
            "detail": f"Port {port}: {'free' if result != 0 else 'IN USE'}",
        }

    # Aggregate
    results["all_ok"] = all(c["ok"] for c in results["checks"].values())
    return results


def print_health_check(console=None):
    """Pretty-print health check results to Rich console."""
    if console is None:
        from rich.console import Console

        console = Console()
    from rich.table import Table

    results = run_health_check()
    table = Table(title="Suijin Health Check")
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Detail", style="dim")
    for name, check in results["checks"].items():
        status = "[green]OK[/green]" if check["ok"] else "[red]FAIL[/red]"
        table.add_row(name, status, check["detail"])
    console.print(table)
    if results["all_ok"]:
        console.print("[green]All checks passed.[/green]")
    else:
        console.print("[yellow]Some checks failed. Run the recommended fixes above.[/yellow]")
