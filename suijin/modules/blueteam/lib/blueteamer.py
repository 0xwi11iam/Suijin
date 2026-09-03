"""
suijin/core/blueteamer.py — Blue Team entry point and TUI.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal as _signal
import sys
import time
from pathlib import Path

_pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _pkg_parent not in sys.path:
    sys.path.insert(0, _pkg_parent)

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from suijin.modules.blueteam.lib.blue.config import load_blue_config
from suijin.modules.blueteam.lib.blue.session_manager import init_session


def _const(name):
    """Platform constant (honours a monkeypatched module attr)."""
    v = globals().get(name)
    if v is not None:
        return v
    from suijin.modules.platform.lib import constants

    return getattr(constants, name)


def __getattr__(name):
    if name in ("BLUE_LAB_PORT", "BLUE_TRAFFIC_LOG", "PROXY_DEFAULT_PORT"):
        return _const(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _load_local_module(name):
    from suijin.modules.loader import load_local_module

    return load_local_module(name)


console = Console()
BASE_DIR = Path(__file__).resolve().parents[3]  # the suijin/ package dir —
# lab apps live at <pkg>/lab, .env at <pkg>/.env (parents[1] pointed into
# modules/blueteam where none of that exists)


def main():
    """Entry point for Blue Team mode."""
    with contextlib.suppress(Exception):  # the dragon at every blue TUI boot
        from suijin.modules.platform.lib.banner import render_boot_banner

        render_boot_banner(console)
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
    _load_local_module("providers")  # module-level registration side effects
    provider = config.get("provider", "deepseek")
    key_var = f"{provider.upper()}_API_KEY"
    if not os.environ.get(key_var) and not os.environ.get("HF_TOKEN"):
        console.print(f"[yellow]No {key_var} in environment. Set it in suijin/.env[/yellow]")

    console.print(
        Panel.fit(
            "[bold #58a6ff]BLUE TEAM — Active Defense[/bold #58a6ff]\n"
            "[dim]Autonomous SOC. Codebase analysis, traffic monitoring, deception, hotfix.[/dim]",
            border_style="#58a6ff",
        )
    )

    # Select target codebase
    console.print("\n[bold white]Select target codebase to defend:[/bold white]")
    console.print("  [bold]1.[/] Type path to codebase")
    console.print("  [bold]2.[/] Use built-in lab (port {})".format(_const("BLUE_LAB_PORT")))
    console.print("  [bold]3.[/] Back to menu")
    try:
        choice = console.input("\n  Choice  ").strip()
    except (KeyboardInterrupt, EOFError):
        return

    target_path = ""
    app_port = _const("BLUE_LAB_PORT")
    traffic_log = str(_const("BLUE_TRAFFIC_LOG"))
    blocking_enabled = False
    proxy_server = None  # Forward proxy for intercepting traffic

    if choice == "1":
        target_path = console.input("  Path to codebase  ").strip()
        if not target_path or not os.path.isdir(target_path):
            console.print("[red]Invalid path.[/red]")
            return

        app_port = int(console.input("  What port does your app run on?  ").strip() or "0")
        if not app_port:
            console.print("[red]Need a port number.[/red]")
            return

        # Auto-detect a free proxy port
        proxy_port = _find_free_port()
        traffic_log = f"/tmp/blue_proxy_{proxy_port}.jsonl"

        # Start the transparent forward proxy
        from suijin.modules.blueteam.lib.blue.proxy import start_proxy

        try:
            proxy_server = start_proxy(
                listen_port=proxy_port,
                target_port=app_port,
                target_host="127.0.0.1",
                log_path=traffic_log,
            )
            console.print(
                f"[green]Proxy started on :{proxy_port}[/green] [dim]-> forwarding to your app on :{app_port}[/dim]"
            )
            console.print(
                f"[bold yellow]Send ALL traffic to http://127.0.0.1:{proxy_port}[/bold yellow] [dim](not :{app_port})[/dim]"
            )
            console.print(
                "[dim]The proxy intercepts every request, logs it for analysis, and forwards to your app.[/dim]"
            )
        except Exception as e:
            console.print(f"[red]Failed to start proxy: {e}[/red]")
            console.print(
                "[yellow]Falling back to log-file mode — start your app with the middleware snippet.[/yellow]"
            )
            _print_middleware_snippet(console, traffic_log)
            console.input("\n  [dim]Press Enter to continue...[/dim]")
    elif choice == "2":
        console.print("\n  [bold white]Built-in labs:[/bold white]")
        console.print("  [bold]1.[/] blue_target (classic, 25 endpoints — port 5906)")
        console.print("  [bold]2.[/] hill_ctf (four perimeters, rotating vault token)")
        try:
            lab_choice = console.input("\n  Lab  ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if lab_choice == "2":
            target_path = str(BASE_DIR / "lab" / "hill_ctf")
            hill_app_port = 5910
            # BF1: the Hill ALWAYS boots behind the proxy — that's the
            # enforcement plane (blocks/honeypots/canaries apply instantly)
            import subprocess
            import urllib.request

            for port in (hill_app_port, 5911):
                try:
                    result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=3)
                    for pid in result.stdout.strip().split("\n"):
                        if pid.strip():
                            os.kill(int(pid.strip()), _signal.SIGTERM)
                except Exception:
                    pass
            time.sleep(0.5)
            subprocess.Popen(
                [sys.executable, str(BASE_DIR / "lab" / "hill_ctf" / "app.py")],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(10):
                time.sleep(0.3)
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{hill_app_port}/health", timeout=1)
                    break
                except Exception:
                    pass
            else:
                console.print("[red]Failed to start the hill lab[/red]")
                return
            from suijin.modules.blueteam.lib.blue.proxy import start_proxy

            # obscure high port (operator request): 8080/5912 collide with
            # dev servers and common tooling; 41732 is unassigned and nobody
            # scans there by default
            proxy_port = _find_free_port(start=41732)
            proxy_server = start_proxy(
                listen_port=proxy_port,
                target_port=hill_app_port,
                target_host="127.0.0.1",
                log_path=_const("BLUE_TRAFFIC_LOG"),
            )
            console.print(
                f"[green]The Hill ready — attack it at :{proxy_port} (app hidden on :{hill_app_port})[/green]"
            )
            console.print(
                "[dim]Your arsenal: blue_block/blue_honeypot/blue_tarpit/… apply at the proxy instantly[/dim]"
            )
            app_port = hill_app_port  # sessions/reporting reference the app port
            traffic_log = str(_const("BLUE_TRAFFIC_LOG"))  # the proxy logs here
        else:
            target_path = str(BASE_DIR / "lab" / "blue_target")
            traffic_log = str(_const("BLUE_TRAFFIC_LOG"))
            app_port = _const("BLUE_LAB_PORT")

            import subprocess
            import urllib.request

            # Kill any stale process on port 5906
            try:
                result = subprocess.run(
                    ["lsof", "-ti", f":{_const('BLUE_LAB_PORT')}"], capture_output=True, text=True, timeout=3
                )
                for pid in result.stdout.strip().split("\n"):
                    pid = pid.strip()
                    if pid:
                        os.kill(int(pid), _signal.SIGTERM)
                        console.print(f"[dim]Killed stale process on :{_const('BLUE_LAB_PORT')} (pid {pid})[/dim]")
                time.sleep(0.5)
            except Exception:
                pass

            # Start the vulnerable app in background
            subprocess.Popen(
                [sys.executable, str(BASE_DIR / "lab" / "blue_target" / "vulnerable_app.py")],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Wait until the app is actually listening
            for _ in range(10):
                time.sleep(0.3)
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{_const('BLUE_LAB_PORT')}/", timeout=1)
                    console.print(f"[green]Vulnerable app ready on port {_const('BLUE_LAB_PORT')}[/green]")
                    break
                except Exception:
                    pass
            else:
                console.print(f"[red]Failed to start vulnerable app on port {_const('BLUE_LAB_PORT')}[/red]")
                return
    else:
        return

    session = init_session(target_path)
    config["target_path"] = target_path

    # Phase 0: Initialize firewall (only if blocking enabled)
    if blocking_enabled:
        _init_firewall(console)
    else:
        console.print("[dim]IP blocking disabled — toggle with /state[/dim]")

    # Phase 1: Codebase analysis
    console.print("\n[bold cyan]Phase 1: Codebase Analysis[/bold cyan]")
    from suijin.modules.blueteam.lib.blue.codebase.scanner import scan_codebase

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
        table.add_row(ep.get("method", "?"), ep.get("path", "?")[:50], ep.get("framework", "?"), ep.get("auth", "?"))
    console.print(table)

    # Phase 1.5: Subagent deployment — one AI subagent per endpoint
    console.print("\n[bold cyan]Phase 1.5: Deploying Endpoint Subagents[/bold cyan]")
    from suijin.modules.blueteam.lib.blue.subagent_manager import SubagentManager

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
        console.print(
            f"    [{color}]Subagent #{ep_risk['rank']}: {ep_risk['path']} (risk {ep_risk['risk']}/10)[/{color}]"
        )

    # Phase 2: Watcher deployment
    console.print("\n[bold cyan]Phase 2: Deploying Watchers[/bold cyan]")
    from suijin.modules.blueteam.lib.blue.watchers import spawn_watchers

    watchers = await spawn_watchers(endpoints, config)
    session.active_watchers = len(watchers)
    console.print(f"  [green]{len(watchers)} watchers deployed across {len(endpoints)} endpoints[/green]")

    # Phase 3: SOC activation
    console.print("\n[bold cyan]Phase 3: SOC Team Activation[/bold cyan]")
    from suijin.modules.blueteam.lib.blue.soc.incident_commander import create_incident_commander
    from suijin.modules.blueteam.lib.blue.soc.soc_lead import activate_soc_lead
    from suijin.modules.blueteam.lib.blue.soc.threat_hunter import create_threat_hunter
    from suijin.modules.blueteam.lib.blue.soc.tier1_analyst import create_tier1
    from suijin.modules.blueteam.lib.blue.soc.tier2_analyst import create_tier2

    soc_lead = await activate_soc_lead(config, asyncio.Queue())
    tier1_analysts = [create_tier1(ep["path"]) for ep in endpoints[:20]]
    tier2 = create_tier2()
    hunter = create_threat_hunter()
    commander = create_incident_commander()

    console.print(f"  [green]SOC Lead online[/green] [dim]({len(soc_lead.campaigns)} campaigns tracked)[/dim]")
    console.print(f"  [green]{len(tier1_analysts)} Tier-1 Analysts deployed[/green]")
    console.print("  [green]Tier-2 Analyst active[/green] [dim](cross-endpoint correlation)[/dim]")
    console.print("  [green]Threat Hunter active[/green] [dim](proactive scanning)[/dim]")
    console.print("  [green]Incident Commander ready[/green]")

    # ── Initialize AI Engine and Live Feed ──
    from suijin.modules.blueteam.lib.blue.ai_engine import BlueAIEngine
    from suijin.modules.blueteam.lib.blue.traffic.normalizer import SmartNormalizer, set_global_normalizer
    from suijin.modules.blueteam.lib.blue.tui.feed import FeedConfig, LiveFeed

    ai_engine = BlueAIEngine(config)
    ai_engine.target_path = target_path  # For code change execution
    normalizer = SmartNormalizer()
    set_global_normalizer(normalizer)

    feed_config = FeedConfig(
        baseline_requests=25,
        ai_analysis_enabled=True,
        show_all_normals=True,
    )
    feed = LiveFeed(
        ai_engine,
        subagent_mgr,
        feed_config,
        soc_lead=soc_lead,
        tier1_analysts=tier1_analysts,
        tier2=tier2,
        threat_hunter=hunter,
        incident_commander=commander,
    )
    feed.blocking_enabled = blocking_enabled

    # ── BF3: the live console session (strip + events + input box) ──
    from suijin.modules.blueteam.lib.blue.session_runner import start_session

    session_ui, cmd_box = start_session(console, target_path.rsplit("/", 1)[-1] or "target", feed=feed)
    session_ui.watchers = len(watchers) if watchers else 0

    # ── Main monitoring loop ──
    # BF3.6: when the live console UI owns the screen, NOTHING may print
    # from this loop's console — foreign prints while the Live strip is
    # active tear the cursor mid-strip (the stacked-spinner bug). The
    # headless path (no feed.ui) keeps the classic prints.
    _ui = getattr(feed, "ui", None)

    _signal.signal(_signal.SIGINT, lambda sig, frame: setattr(_signal, "_blue_interrupted", True))
    _signal._blue_interrupted = False

    # Tail the live traffic log (configurable path)
    # The GLOBAL /tmp traffic log starts clean each session (stale entries
    # from previous runs poisoned the tail). The BF5-retained workspace
    # log (outputs/blue_traffic/) is separate and NEVER truncated.
    open(traffic_log, "w").close()

    request_count = 0
    last_pos = 0
    idle_ticks = 0

    if _ui is None:
        console.print(
            "\n[bold #58a6ff]Live Traffic Feed[/bold #58a6ff] [dim](Ctrl+C to pause, type commands anytime)[/dim]"
        )
        console.print("─" * 68)
        if app_port:
            console.print(
                f"  [bold green]Listening on :{app_port}[/bold green] [dim]— traffic log: {traffic_log}[/dim]"
            )
        else:
            console.print(f"  [bold green]Monitoring[/bold green] [dim]— traffic log: {traffic_log}[/dim]")
        console.print(
            f"  [dim]Send HTTP requests to the target app. Blocking: {'ON' if blocking_enabled else 'OFF'}[/dim]"
        )
        console.print("─" * 68)
    else:
        where = f":{app_port}" if app_port else "log"
        session_ui.banner(
            f"Live Traffic Feed — {where} · traffic log: {traffic_log}\n"
            f"Blocking: {'ON' if blocking_enabled else 'OFF'} · Ctrl+C pauses · type commands anytime"
        )

    while True:
        # Read new lines from traffic log
        try:
            with open(traffic_log) as f:
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

            # Update session from feed stats — BF0: DETECTED (flagged) is
            # not BLOCKED; enforcement counters come from the feed's own
            # honest tally of applied defenses
            if result and result.verdict == "FLAGGED":
                session.threats_blocked += 1
            fs = feed.get_stats()
            session.threats_deceived = fs.get("deceived", 0)

            # BF3: strip sync — the live UI shows what actually happened
            session_ui.requests = request_count
            session_ui.detected = session.threats_blocked
            session_ui.tarpitted = fs.get("tarpitted", 0)
            session_ui.blocked = fs.get("blocked", 0)
            session_ui.deceived = session.threats_deceived
            session_ui.cost_usd = fs.get("ai_cost", 0.0)
            session_ui.tick()

            if request_count % 25 == 0 and request_count > 0 and _ui is None:
                stats = feed.get_stats()
                console.print(
                    f"  [dim]── {request_count} requests | "
                    f"{session.threats_blocked} detected | "
                    f"{stats.get('tarpitted', 0)} tarpitted | "
                    f"{stats.get('blocked', 0)} blocked | "
                    f"{session.threats_deceived} deceived | "
                    f"${stats['ai_cost']:.4f} AI cost ──[/dim]"
                )

        if not new_lines:
            idle_ticks += 1
            if idle_ticks % 15 == 0 and _ui is None:
                # headless nudge only — the live console shows baseline
                # progress in the strip and never prints idle chatter
                if not feed.baseline_established:
                    remaining = feed_config.baseline_requests - request_count
                    console.print(f"  [dim]  establishing baseline... {remaining} more requests needed[/dim]")
                else:
                    port_str = f":{app_port}" if app_port else ""
                    console.print(f"  [dim]  listening{port_str} — send traffic from another terminal[/dim]")

        await asyncio.sleep(0.3)

        # ── Pause / Command handling ──
        if getattr(_signal, "_blue_interrupted", False):
            _signal._blue_interrupted = False
            _signal.signal(_signal.SIGINT, _signal.SIG_DFL)
            # BF3.6: the keystroke reader must yield the terminal to this
            # prompt (cbreak echo off garbles console.input) — suspend it
            with contextlib.suppress(Exception):
                cmd_box.suspend()
            try:
                console.print("\n[yellow]  Paused[/yellow] [dim](/report /state /template /health /quit)[/dim]")
                cmd = console.input("[bold cyan]  Command  [/bold cyan]").strip()
            except (KeyboardInterrupt, EOFError):
                cmd = "/resume-io"  # still resume the reader below, then exit
            with contextlib.suppress(Exception):
                cmd_box.resume()
            if cmd in ("/resume-io", "/quit"):
                break
            elif cmd == "/health":
                from suijin.modules.platform.lib.templates import print_health_check

                print_health_check(console)
            elif cmd == "/report":
                stats = feed.get_stats()
                console.print(f"  [dim]Requests: {stats['total']} | AI analyses: {stats['ai_analyses']}[/dim]")
                console.print(
                    f"  [dim]Subagents: {stats['subagents']['total']} | High risk: {stats['subagents']['high_risk']}[/dim]"
                )
                console.print(f"  [dim]AI cost: ${stats['ai_cost']:.4f}[/dim]")
            elif cmd == "/state":
                stats = feed.get_stats()
                console.print(f"  Endpoints: {session.endpoints_discovered}")
                console.print(f"  Requests: {session.total_requests_processed}")
                console.print(f"  Watchers: {session.active_watchers}")
                console.print(f"  Subagents: {stats['subagents']['total']}")
                console.print(f"  AI Analyses: {stats['ai_analyses']}")
                console.print(
                    f"  Baseline: {'established' if feed.baseline_established else f'{request_count}/{feed_config.baseline_requests}'}"
                )
                console.print(f"  Traffic log: {traffic_log}")
                console.print(
                    f"  Blocking: {'[green]ON[/green]' if blocking_enabled else '[red]OFF[/red]'} (toggle with /block)"
                )
                console.print(f"  Cost: ${stats['ai_cost']:.4f}")
            elif cmd == "/block":
                blocking_enabled = not blocking_enabled
                feed.blocking_enabled = blocking_enabled
                console.print(
                    f"  IP blocking: {'[green]ENABLED[/green]' if blocking_enabled else '[red]DISABLED[/red]'}"
                )
            _signal.signal(_signal.SIGINT, lambda sig, frame: setattr(_signal, "_blue_interrupted", True))
            continue

    # BF3: session teardown — strip off, input box off, clean exit
    cmd_box.stop()
    session_ui.stop()
    session_ui.banner("session ended — report in outputs/blue_state/", "yellow")

    session.save()
    # Shut down proxy if running
    if proxy_server:
        try:
            proxy_server.stop()
            console.print("[dim]Proxy shut down.[/dim]")
        except Exception:
            pass
    console.print("[dim]Blue team session ended.[/dim]")


def _find_free_port(start: int = _const("PROXY_DEFAULT_PORT"), max_attempts: int = 20) -> int:
    """Find a free TCP port."""
    import socket

    for port in range(start, start + max_attempts):
        try:
            s = socket.socket()
            s.settimeout(0.1)
            s.bind(("", port))
            s.close()
            return port
        except OSError:
            continue
    return _const("PROXY_DEFAULT_PORT")  # fallback


def _print_middleware_snippet(console, log_path: str):
    """Print a Flask middleware snippet the user can add to their app."""
    console.print(
        Panel.fit(
            f"""[bold white]Add this to your Flask app to send traffic to Suijin:[/bold white]

[dim]# At the top of your app.py:[/dim]
[bold]import json, os[/bold]
[bold]SUIJIN_LOG = "{log_path}"[/bold]

[dim]# Add before_request handler:[/dim]
[bold]@app.before_request[/bold]
[bold]def suijin_log_request():[/bold]
[bold]    entry = {{[/bold]
[bold]        "timestamp": __import__('datetime').datetime.now().isoformat(),[/bold]
[bold]        "method": request.method,[/bold]
[bold]        "path": request.path,[/bold]
[bold]        "query": dict(request.args),[/bold]
[bold]        "body": request.get_data(as_text=True)[:1000],[/bold]
[bold]        "ip": request.remote_addr,[/bold]
[bold]        "user_agent": str(request.user_agent),[/bold]
[bold]        "headers": {{k: v for k, v in request.headers.items()[/bold]
[bold]                    if k.lower() in ("content-type", "cookie", "authorization")}},[/bold]
[bold]    }}[/bold]
[bold]    with open(SUIJIN_LOG, "a") as f:[/bold]
[bold]        f.write(json.dumps(entry) + "\\n")[/bold]

[green]Paste this into your app, restart it, then press Enter to continue.[/green]""",
            border_style="green",
        )
    )


def _init_firewall(console):
    """Create pfctl table (macOS) or iptables chain (Linux) for IP blocking."""
    import platform
    import subprocess

    system = platform.system()
    if system == "Darwin":
        try:
            # Create pf anchor and table if they don't exist
            subprocess.run(
                ["sudo", "pfctl", "-t", "blue_blocked", "-T", "add", "255.255.255.255"], capture_output=True, timeout=5
            )
            subprocess.run(
                ["sudo", "pfctl", "-t", "blue_blocked", "-T", "delete", "255.255.255.255"],
                capture_output=True,
                timeout=5,
            )
            console.print("[dim]pfctl table 'blue_blocked' ready[/dim]")
        except Exception:
            console.print("[dim]pfctl unavailable — blocking via subprocess only[/dim]")
    else:
        try:
            subprocess.run(["sudo", "iptables", "-N", "BLUE_BLOCKED"], capture_output=True, timeout=5)
            subprocess.run(["sudo", "iptables", "-A", "INPUT", "-j", "BLUE_BLOCKED"], capture_output=True, timeout=5)
            console.print("[dim]iptables chain 'BLUE_BLOCKED' ready[/dim]")
        except Exception:
            console.print("[dim]iptables unavailable — blocking via subprocess only[/dim]")


if __name__ == "__main__":
    main()
