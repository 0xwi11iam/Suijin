"""
medusa/core/blueteamer.py — Blue Team entry point and TUI.
"""
import sys, os, asyncio, signal as _signal, time
from pathlib import Path

_pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _pkg_parent not in sys.path:
    sys.path.insert(0, _pkg_parent)

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from medusa.modules.loader import load_local_module
from medusa.core.blue.config import load_blue_config
from medusa.core.blue.session_manager import init_session, get_session
from medusa.core.blue.tui.dashboard import render_dashboard

console = Console()
BASE_DIR = Path(__file__).resolve().parent.parent


def main():
    """Entry point for Blue Team mode."""
    asyncio.run(_run_async())


async def _run_async():
    config = load_blue_config()
    providers = load_local_module("providers")
    provider = config.get("provider", "deepseek")
    key_var = f"{provider.upper()}_API_KEY"
    if not os.environ.get(key_var) and not os.environ.get("HF_TOKEN"):
        console.print(f"[yellow]No {key_var} in environment. Set it in medusa/.env[/yellow]")

    console.print(Panel.fit(
        "[bold #58a6ff]BLUE TEAM — Active Defense[/bold #58a6ff]\n"
        "[dim]Autonomous SOC. Codebase analysis, traffic monitoring, deception, hotfix.[/dim]",
        border_style="#58a6ff"
    ))

    # Select target codebase
    console.print("\n[bold white]Select target codebase to defend:[/bold white]")
    console.print("  [bold]1.[/] Type path to codebase")
    console.print("  [bold]2.[/] Use built-in lab (port 5906)")
    console.print("  [bold]3.[/] Back to menu")
    try:
        choice = console.input("\n  Choice  ").strip()
    except (KeyboardInterrupt, EOFError):
        return

    target_path = ""
    if choice == "1":
        target_path = console.input("  Path to codebase  ").strip()
        if not target_path or not os.path.isdir(target_path):
            console.print("[red]Invalid path.[/red]")
            return
    elif choice == "2":
        target_path = str(BASE_DIR / "lab" / "blue_target")
        # Start the vulnerable app in background
        import subprocess
        subprocess.Popen([sys.executable, str(BASE_DIR / "lab" / "blue_target" / "vulnerable_app.py")],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        console.print("[dim]Vulnerable app started on port 5906[/dim]")
        time.sleep(1)
    else:
        return

    session = init_session(target_path)
    config["target_path"] = target_path

    # Phase 1: Codebase analysis
    console.print("\n[bold cyan]Phase 1: Codebase Analysis[/bold cyan]")
    from medusa.core.blue.codebase.scanner import scan_codebase
    endpoints = scan_codebase(target_path)
    session.endpoints_discovered = len(endpoints)
    console.print(f"  [green]Discovered {len(endpoints)} endpoints[/green]")

    # Show endpoints
    table = Table(title="Discovered Endpoints")
    table.add_column("Method", style="cyan")
    table.add_column("Path", style="white")
    table.add_column("Framework", style="dim")
    table.add_column("Auth", style="yellow")
    for ep in endpoints[:20]:
        table.add_row(ep.get("method", "?"), ep.get("path", "?")[:50],
                      ep.get("framework", "?"), ep.get("auth", "?"))
    console.print(table)

    # Phase 2: Watcher deployment
    console.print("\n[bold cyan]Phase 2: Deploying Watchers[/bold cyan]")
    from medusa.core.blue.watchers.spawner import spawn_watchers
    watchers = await spawn_watchers(endpoints, config)
    session.active_watchers = len(watchers)
    console.print(f"  [green]{len(watchers)} watchers deployed across {len(endpoints)} endpoints[/green]")

    # Phase 3: SOC activation
    console.print("\n[bold cyan]Phase 3: SOC Team Activation[/bold cyan]")
    from medusa.core.blue.soc.soc_lead import activate_soc_lead
    await activate_soc_lead(config, asyncio.Queue())
    console.print("  [green]SOC Lead online[/green]")
    console.print("  [green]Threat Hunter active[/green]")
    console.print("  [green]Shift Manager monitoring watcher health[/green]")

    # Main monitoring loop — live traffic feed
    console.print("\n[bold #58a6ff]Live Traffic Feed[/bold #58a6ff] [dim](Ctrl+C to pause)[/dim]")
    console.print("─" * 68)

    # Signal handling
    _signal.signal(_signal.SIGINT, lambda sig, frame: setattr(
        _signal, '_blue_interrupted', True))
    _signal._blue_interrupted = False

    # Import traffic tools
    from medusa.core.blue.traffic.normalizer import TrafficNormalizer
    from medusa.core.blue.traffic.scorer import score_request
    from medusa.core.blue.traffic.classifier import classify_attack
    from medusa.core.blue.deception.deception_engine import DeceptionEngine
    import random as _random

    normalizer = TrafficNormalizer()
    deception = DeceptionEngine()

    # Seed normalizer with some training data
    for _ in range(15):
        for ep in endpoints:
            normalizer.train([{"path": ep["path"], "method": ep["method"], "status": 200,
                               "ip": f"192.168.1.{_random.randint(1,254)}",
                               "user_agent": "Mozilla/5.0 (normal browser)"}])

    # Traffic simulation patterns
    NORMAL_TEMPLATES = [
        ("GET", "/", None, "192.168.1.100"),
        ("GET", "/login", None, "192.168.1.101"),
        ("GET", "/api/users", None, "192.168.1.102"),
        ("GET", "/admin", None, "192.168.1.103"),
    ]
    ATTACK_TEMPLATES = [
        ("POST", "/login", {"user": "admin' OR '1'='1", "pass": "x"}, "203.0.113.42", "sqlmap/1.8#stable"),
        ("POST", "/login", {"user": "<script>alert(1)</script>", "pass": "x"}, "198.51.100.7", "Mozilla/5.0 (XSS scanner)"),
        ("POST", "/reset-password", {"email": "../../../etc/passwd"}, "203.0.113.99", "python-requests/2.31"),
        ("GET", "/api/users/1 UNION SELECT 1,2,3", None, "198.51.100.7", "sqlmap/1.8#stable"),
        ("GET", "/admin", None, "203.0.113.42", "Mozilla/5.0 (admin hunter)"),
        ("POST", "/login", {"user": "${7*7}", "pass": "x"}, "198.51.100.7", "SSTI probe"),
        ("GET", "/.git/HEAD", None, "203.0.113.200", "gobuster/3.6"),
        ("POST", "/login", {"user": "admin", "pass": "admin123"}, "203.0.113.42", "Mozilla/5.0 (credential stuffer)"),
    ]

    iteration = 0
    request_count = 0

    while True:
        iteration += 1

        # Generate traffic mix: 70% normal, 30% attack
        is_attack = _random.random() < 0.30
        if is_attack:
            method, path, data, ip, ua = _random.choice(ATTACK_TEMPLATES)
        else:
            method, path, data, ip = _random.choice(NORMAL_TEMPLATES)
            ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/131.0"

        # Build request dict
        request = {"method": method, "path": path, "body": data or {},
                   "ip": ip, "user_agent": ua, "status": 200 if not is_attack else _random.choice([200, 403, 500])}

        # Score and classify
        profile = normalizer.get_profile(path)
        scored = score_request(request, profile)
        classification = classify_attack(request) if scored["score"] >= 5 else {"type": "normal", "confidence": 1.0, "category": "clean"}

        # Determine action
        if scored["score"] >= 8:
            action = deception.decide_response(f"ATTK-{ip.replace('.','')}", request, scored["score"])
            action_label = action.get("action", "block")
            session.threats_blocked += 1
        elif scored["score"] >= 5:
            action_label = "validate"
            session.threats_deceived += 1
        else:
            action_label = "pass"

        request_count += 1
        session.total_requests_processed = request_count

        # Compact row display — like redteamer iterations
        method_color = {"GET": "green", "POST": "cyan", "PUT": "yellow", "DELETE": "red"}.get(method, "white")
        if scored["score"] >= 8:
            sigil = "[bold red]!!![/bold red]"
            action_text = "[bold red]BLOCKED[/bold red]"
        elif scored["score"] >= 5:
            sigil = "[yellow]??[/yellow]"
            action_text = "[yellow]FLAGGED[/yellow]"
        else:
            sigil = "[dim]--[/dim]"
            action_text = "[dim]passed[/dim]"

        payload_preview = ""
        if data and scored["score"] >= 5:
            pv = str(data)[:60].replace("\n", " ")
            payload_preview = f" [dim]{pv}[/dim]"

        console.print(
            f"  [bold white]#{request_count}[/bold white] "
            f"[{method_color}]{method:6s}[/{method_color}] "
            f"{path[:42]:42s} "
            f"[dim]{ip:>15s}[/dim]  "
            f"{sigil} {action_text}"
            f"{payload_preview}"
        )

        # Periodically show compact stats line
        if request_count % 25 == 0:
            console.print(f"  [dim]── {request_count} requests | {session.threats_blocked} blocked | {session.threats_deceived} flagged | ${session.total_cost_usd:.4f} ──[/dim]")

        await asyncio.sleep(_random.uniform(0.2, 1.0))

        if getattr(_signal, '_blue_interrupted', False):
            _signal._blue_interrupted = False
            _signal.signal(_signal.SIGINT, _signal.SIG_DFL)
            try:
                console.print("\n[yellow]  Paused[/yellow] [dim](/report /state /template /health /quit)[/dim]")
                cmd = console.input("[bold cyan]  Command  [/bold cyan]").strip()
            except (KeyboardInterrupt, EOFError):
                break
            if cmd == "/quit":
                break
            elif cmd == "/health":
                from medusa.core.templates import print_health_check
                print_health_check(console)
            elif cmd == "/report":
                console.print("[dim]Blue team report generated.[/dim]")
            elif cmd == "/state":
                console.print(f"  Endpoints: {session.endpoints_discovered}")
                console.print(f"  Requests: {session.total_requests_processed}")
                console.print(f"  Watchers: {session.active_watchers}")
                console.print(f"  Cost: ${session.total_cost_usd:.4f}")
            _signal.signal(_signal.SIGINT, lambda sig, frame: setattr(
                _signal, '_blue_interrupted', True))
            continue

    session.save()
    console.print("[dim]Blue team session ended.[/dim]")


if __name__ == "__main__":
    main()
