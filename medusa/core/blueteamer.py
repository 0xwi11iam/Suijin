"""
medusa/core/blueteamer.py — Blue Team entry point and TUI.
"""
import sys, os, asyncio, signal as _signal, time, json
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
    # Load .env first
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

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

        import subprocess, urllib.request

        # Kill any stale process on port 5906
        try:
            result = subprocess.run(
                ["lsof", "-ti", ":5906"], capture_output=True, text=True, timeout=3
            )
            for pid in result.stdout.strip().split("\n"):
                pid = pid.strip()
                if pid:
                    os.kill(int(pid), _signal.SIGTERM)
                    console.print(f"[dim]Killed stale process on :5906 (pid {pid})[/dim]")
            time.sleep(0.5)
        except Exception:
            pass

        # Start the vulnerable app in background
        subprocess.Popen(
            [sys.executable, str(BASE_DIR / "lab" / "blue_target" / "vulnerable_app.py")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # Wait until the app is actually listening
        for _ in range(10):
            time.sleep(0.3)
            try:
                urllib.request.urlopen("http://127.0.0.1:5906/", timeout=1)
                console.print("[green]✓ Vulnerable app ready on port 5906[/green]")
                break
            except Exception:
                pass
        else:
            console.print("[red]✗ Failed to start vulnerable app on port 5906[/red]")
            return
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

    # Show endpoints table
    table = Table(title="Discovered Endpoints")
    table.add_column("Method", style="cyan")
    table.add_column("Path", style="white")
    table.add_column("Framework", style="dim")
    table.add_column("Auth", style="yellow")
    for ep in endpoints[:20]:
        table.add_row(ep.get("method", "?"), ep.get("path", "?")[:50],
                      ep.get("framework", "?"), ep.get("auth", "?"))
    console.print(table)

    # Phase 1.5: Subagent deployment — one AI subagent per endpoint
    console.print("\n[bold cyan]Phase 1.5: Deploying Endpoint Subagents[/bold cyan]")
    from medusa.core.blue.subagent_manager import SubagentManager

    subagent_mgr = SubagentManager(config, target_path)
    deployed = subagent_mgr.deploy_all(endpoints)
    session.subagents_deployed = len(deployed)
    console.print(f"  [green]{len(deployed)} subagents deployed[/green] [dim](one per endpoint)[/dim]")

    # Have each subagent analyze its endpoint (batched, parallel)
    console.print("  [dim]Subagents analyzing their endpoints...[/dim]")
    analyzed = await subagent_mgr.analyze_all_endpoints()
    console.print(f"  [green]{len(analyzed)} endpoint analyses complete[/green]")

    # Show risk summary
    risk_summary = subagent_mgr.get_summary()
    high_risk = risk_summary.get("high_risk", 0)
    if high_risk > 0:
        console.print(f"  [yellow]{high_risk} high-risk endpoints identified[/yellow]")
    for ep_risk in risk_summary.get("by_risk", [])[:5]:
        color = "red" if ep_risk["risk"] >= 7 else "yellow" if ep_risk["risk"] >= 4 else "dim"
        console.print(f"    [{color}]Subagent #{ep_risk['rank']}: {ep_risk['path']} (risk {ep_risk['risk']}/10)[/{color}]")

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

    # ── Initialize AI Engine and Live Feed ──
    from medusa.core.blue.ai_engine import BlueAIEngine
    from medusa.core.blue.tui.feed import LiveFeed, FeedConfig
    from medusa.core.blue.traffic.normalizer import SmartNormalizer, set_global_normalizer

    ai_engine = BlueAIEngine(config)
    ai_engine.target_path = target_path  # For code change execution
    normalizer = SmartNormalizer()
    set_global_normalizer(normalizer)

    feed_config = FeedConfig(
        baseline_requests=25,
        ai_analysis_enabled=True,
        show_all_normals=True,
    )
    feed = LiveFeed(ai_engine, subagent_mgr, feed_config)

    # ── Main monitoring loop ──
    console.print("\n[bold #58a6ff]Live Traffic Feed[/bold #58a6ff] [dim](Ctrl+C to pause)[/dim]")
    console.print("─" * 68)

    # Signal handling
    _signal.signal(_signal.SIGINT, lambda sig, frame: setattr(
        _signal, '_blue_interrupted', True))
    _signal._blue_interrupted = False

    # Tail the live traffic log
    TRAFFIC_LOG = "/tmp/blue_defend_traffic.jsonl"
    open(TRAFFIC_LOG, "w").close()  # clear old log

    request_count = 0
    last_pos = 0
    idle_ticks = 0

    console.print("  [bold green]Listening on :5906[/bold green] [dim]— send requests from another terminal:[/dim]")
    console.print("  [dim]curl http://127.0.0.1:5906/[/dim]")
    console.print("  [dim]curl -X POST http://127.0.0.1:5906/login -d \"user=admin' OR '1'='1&pass=x\"[/dim]")
    console.print("─" * 68)

    while True:
        # Read new lines from traffic log
        try:
            with open(TRAFFIC_LOG) as f:
                f.seek(last_pos)
                new_lines = f.readlines()
                last_pos = f.tell()
        except FileNotFoundError:
            await asyncio.sleep(0.5)
            continue

        for line in new_lines:
            line = line.strip()
            if not line:
                continue

            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue

            idle_ticks = 0

            # Build request dict
            request_data = {
                "method": req.get("method", "GET"),
                "path": req.get("path", "/"),
                "ip": req.get("ip", "0.0.0.0"),
                "body": req.get("body", ""),
                "user_agent": req.get("user_agent", ""),
                "query": req.get("query", {}),
                "headers": req.get("headers", {}),
                "status": 200,
            }

            # Train normalizer during baseline phase
            if not feed.baseline_established:
                normalizer.train([request_data])

            # Route through the live feed tier system
            result = await feed.process_request(request_data)

            request_count = feed.request_count
            session.total_requests_processed = request_count
            session.baseline_established = feed.baseline_established
            session.baseline_request_count = request_count

            # Update session from feed stats
            if result and result.verdict == "FLAGGED":
                session.threats_blocked += 1

            if request_count % 25 == 0 and request_count > 0:
                stats = feed.get_stats()
                console.print(
                    f"  [dim]── {request_count} requests | "
                    f"{session.threats_blocked} blocked | "
                    f"{session.threats_deceived} flagged | "
                    f"${stats['ai_cost']:.4f} AI cost ──[/dim]"
                )

        if not new_lines:
            idle_ticks += 1
            if idle_ticks % 15 == 0:
                if not feed.baseline_established:
                    remaining = feed_config.baseline_requests - request_count
                    console.print(f"  [dim]  establishing baseline... {remaining} more requests needed[/dim]")
                else:
                    console.print(f"  [dim]  listening on :5906 — send traffic from another terminal[/dim]")

        await asyncio.sleep(0.3)

        # ── Pause / Command handling ──
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
                stats = feed.get_stats()
                console.print(f"  [dim]Requests: {stats['total']} | AI analyses: {stats['ai_analyses']}[/dim]")
                console.print(f"  [dim]Subagents: {stats['subagents']['total']} | High risk: {stats['subagents']['high_risk']}[/dim]")
                console.print(f"  [dim]AI cost: ${stats['ai_cost']:.4f}[/dim]")
            elif cmd == "/state":
                stats = feed.get_stats()
                console.print(f"  Endpoints: {session.endpoints_discovered}")
                console.print(f"  Requests: {session.total_requests_processed}")
                console.print(f"  Watchers: {session.active_watchers}")
                console.print(f"  Subagents: {stats['subagents']['total']}")
                console.print(f"  AI Analyses: {stats['ai_analyses']}")
                console.print(f"  Baseline: {'established' if feed.baseline_established else f'{request_count}/{feed_config.baseline_requests}'}")
                console.print(f"  Cost: ${stats['ai_cost']:.4f}")
            _signal.signal(_signal.SIGINT, lambda sig, frame: setattr(
                _signal, '_blue_interrupted', True))
            continue

    session.save()
    console.print("[dim]Blue team session ended.[/dim]")


if __name__ == "__main__":
    main()
