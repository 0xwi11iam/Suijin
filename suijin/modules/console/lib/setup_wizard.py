"""suijin setup — the 30-second configuration wizard (2026-11-05).

For professionals: no hand-holding, no tutorials. Provider → key →
model → effort → verify. Sequential, one screen at a time, ends with
a live provider test and a summary of what was saved.

suijin providers --test — one command that tells you which providers
are alive (1-token live request through each).
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time


def setup_complete_marker():
    """The explicit 'operator finished setup' signal. config.json is
    pre-created with defaults by init_runtime BEFORE the first-boot
    gate can look at it — only this marker proves a human completed
    the wizard."""
    from pathlib import Path

    from suijin.modules.console.lib import settings_tui as _st

    return _st.CONFIG_PATH.parent / ".setup-complete"


def run_setup() -> int:
    """The setup wizard — sequential screens, exits when done."""
    from rich.console import Console
    from rich.panel import Panel

    from suijin.modules.console.lib import settings_tui as st

    console = Console()
    console.print(
        Panel(
            "[bold white]suijin setup[/bold white]\n"
            "Configure your provider, key, model, and effort.\n"
            "Everything saves to ~/.suijin/config/config.json",
            title=" setup ",
            border_style="cyan",
        )
    )

    config, status = st.load_state()
    if status == "corrupt":
        console.print("[bold red]config.json is corrupt — fix it manually, then re-run setup[/bold red]")
        return 1

    changed = False

    # ── 1. Provider ──
    from suijin.modules.providers.lib.registry import PROVIDER_REGISTRY

    providers = sorted(PROVIDER_REGISTRY.keys())
    console.print("\n[bold]Provider[/bold] — pick one:")
    for i, p in enumerate(providers, 1):
        spec = PROVIDER_REGISTRY[p]
        note = str(getattr(spec, "note", "") or "")[:60]
        key_s = st.provider_key_status(p, config)
        console.print(f"  [cyan]{i:2d}.[/cyan] {p:<16} {key_s}  [dim]{note}[/dim]")

    try:
        raw = console.input("\n[dim]number (Enter = keep current):[/dim] ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]cancelled[/dim]")
        return 130

    if raw.isdigit() and 1 <= int(raw) <= len(providers):
        picked = providers[int(raw) - 1]
        if picked != config.get("provider"):
            config["provider"] = picked
            changed = True
            console.print(f"  [green]→ {picked}[/green]")
    console.print(f"  provider: [bold]{config.get('provider', '—')}[/bold]")

    prov = str(config.get("provider") or "")

    # ── 2. API Key ──
    key_status = st.provider_key_status(prov, config)
    if "✗" in key_status:
        console.print(f"\n[bold]{prov}[/bold] — no API key on file.")
        try:
            key = console.input("  Paste your API key (hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            key = ""
        if key:
            from suijin.modules.providers.lib import set_provider_key

            ok, msg = set_provider_key(prov, key)
            if ok:
                console.print("  [green]✓ key saved[/green]")
            else:
                console.print(f"  [red]key not saved: {msg}[/red]")
    else:
        console.print(f"\n[bold]{prov}[/bold] — {key_status} [dim](skip key entry)[/dim]")

    # ── 3. Model ──
    console.print(f"\n[bold]Model for {prov}[/bold] — fetching live ids…")
    ids, fetch_note = st.fetch_model_ids(prov, config)
    current_model = str(config.get(f"{prov}_model") or "")
    if ids:
        from suijin.modules.providers.lib import model_meta as _mm

        shown = ids[:20]
        if current_model and current_model not in shown:
            shown = [current_model] + shown
        for i, mid in enumerate(shown, 1):
            mark = " [green]← current[/green]" if mid == current_model else ""
            meta = ""
            with contextlib.suppress(Exception):
                price = _mm.resolve_pricing(prov, mid, config)
                win = _mm.resolve_context_window(prov, mid, config)
                if price:
                    meta = f"  [dim]${price[0]:.2f}/${price[1]:.2f} per 1M[/dim]"
                if win:
                    meta += f" [dim]{win // 1000}k ctx[/dim]"
            console.print(f"  [cyan]{i:2d}.[/cyan] {mid}{meta}{mark}")
        try:
            raw = console.input(f"\n[dim]number 1-{len(shown)} (or paste any id, Enter = keep current):[/dim] ").strip()
        except (EOFError, KeyboardInterrupt):
            raw = ""
        if raw.isdigit() and 1 <= int(raw) <= len(shown):
            model = shown[int(raw) - 1]
        elif raw:
            model = raw
        else:
            model = current_model
        if model and model != current_model:
            config[f"{prov}_model"] = model
            changed = True
            console.print(f"  [green]→ {model}[/green]")
    else:
        console.print(f"  [yellow]couldn't fetch live ids: {fetch_note}[/yellow]")
        try:
            model = console.input("  Type or paste a model id (Enter = keep): ").strip()
        except (EOFError, KeyboardInterrupt):
            model = ""
        if model and model != current_model:
            config[f"{prov}_model"] = model
            changed = True
    console.print(f"  model: [bold]{config.get(f'{prov}_model', '— (registry default)')}[/bold]")

    # ── 4. Effort ──
    tiers = ["low", "medium", "high", "max"]
    current_effort = str(config.get("intelligence") or "max")
    console.print("\n[bold]Effort[/bold] — reasoning depth (affects speed and cost):")
    for i, t in enumerate(tiers, 1):
        mark = " [green]← current[/green]" if t == current_effort else ""
        desc = {
            "low": "fast, minimal thinking",
            "medium": "balanced",
            "high": "deep reasoning",
            "max": "maximum depth (slowest, most expensive)",
        }[t]
        console.print(f"  [cyan]{i}.[/cyan] {t:<8} [dim]{desc}[/dim]{mark}")
    try:
        raw = console.input(f"\n[dim]1-4 (Enter = keep {current_effort}):[/dim] ").strip()
    except (EOFError, KeyboardInterrupt):
        raw = ""
    if raw.isdigit() and 1 <= int(raw) <= 4:
        effort = tiers[int(raw) - 1]
        if effort != current_effort:
            config["intelligence"] = effort
            changed = True
            console.print(f"  [green]→ {effort}[/green]")
    console.print(f"  effort: [bold]{config.get('intelligence', 'max')}[/bold]")

    # ── 5. Mode ── (basic vs advanced — plain language, no jargon)
    current_mode = str(config.get("operator_mode") or "basic")
    console.print("\n[bold]How do you want to work?[/bold]")
    console.print("  [cyan]1.[/cyan] basic     one agent, the box, normal testing. no swarm.")
    console.print("  [cyan]2.[/cyan] advanced  swarms of agents, sandboxes, live steering.")
    try:
        raw = console.input(f"\n[dim]1-2 (Enter = keep {current_mode}):[/dim] ").strip()
    except (EOFError, KeyboardInterrupt):
        raw = ""
    if raw in ("1", "2"):
        mode = "basic" if raw == "1" else "advanced"
        if mode != current_mode:
            config["operator_mode"] = mode
            changed = True
            console.print(f"  [green]→ {mode}[/green]")
    console.print(f"  mode: [bold]{config.get('operator_mode', 'basic')}[/bold]")
    if config.get("operator_mode") == "advanced":
        console.print("  [dim]swarm tools unlocked: suijin swarm … · suijin sandbox …[/dim]")

    # ── 6. Engine probe ── (sandboxes need a container engine; optional)
    engine = "none"
    with contextlib.suppress(Exception):
        from suijin.server.box.engine import detect_engine

        engine = detect_engine(interactive=False)
    console.print(f"\n[bold]Container engine[/bold] — needed only for sandboxed agents: [bold]{engine}[/bold]")
    if engine == "none":
        console.print("  [dim]optional: install OrbStack (mac) or docker to enable sandboxes[/dim]")

    # ── 7. Browser layer offer ── (cookie jar / DOM testing; optional)
    have_browser = False
    with contextlib.suppress(Exception):
        import importlib.util

        have_browser = importlib.util.find_spec("playwright") is not None
    if not have_browser:
        console.print("\n[bold]Browser layer[/bold] — live cookie jar + page testing (optional).")
        try:
            raw = console.input("  install now? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            raw = ""
        if raw == "y":
            console.print("  installing suijin[browser] (playwright + patchright)…")
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "playwright", "patchright"],
                capture_output=True, text=True, timeout=600,
            )
            if r.returncode == 0:
                console.print("  [green]✓ installed[/green] — fetch engines: playwright install chromium")
            else:
                console.print(f"  [red]install failed: {r.stderr[-120:]}[/red]")
        else:
            console.print("  [dim]skipped — add later: pip install suijin[browser][/dim]")
    else:
        console.print("\n[bold]Browser layer[/bold]: [green]installed ✓[/green] — [dim]suijin auth to capture sessions[/dim]")

    # ── 8. Save ──
    if changed:
        st.save_config(config)
        console.print(f"\n[green]✓ saved[/green] → {st.CONFIG_PATH}")
    else:
        console.print("\n[dim]no changes[/dim]")

    # ── 9. Live test ──
    console.print("\n[bold]Testing provider…[/bold]")
    result = _test_one_provider(prov, config)
    console.print(f"  {result}")

    # ── Summary ──
    console.print(
        Panel(
            f"  provider:  [bold]{prov}[/bold]\n"
            f"  model:     [bold]{config.get(f'{prov}_model', '—')}[/bold]\n"
            f"  effort:    [bold]{config.get('intelligence', 'max')}[/bold]\n"
            f"  mode:      [bold]{config.get('operator_mode', 'basic')}[/bold]\n"
            f"  engine:    [bold]{engine}[/bold]\n"
            f"  api key:   {st.provider_key_status(prov, config)}\n"
            f"  config:    {st.CONFIG_PATH}\n\n"
            f"  Hunt:      [cyan]suijin engage \"test http://example.com\"[/cyan]\n"
            f"  Sessions:  [cyan]suijin auth[/cyan]  (live cookie jar)\n"
            + (
                f"  Swarms:    [cyan]suijin swarm console[/cyan] · [cyan]suijin swarm spawn --sandbox N[/cyan]\n"
                if config.get("operator_mode") == "advanced"
                else ""
            ),
            title=" ready ",
            border_style="green",
        )
    )
    with contextlib.suppress(Exception):
        setup_complete_marker().write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
    return 0


def run_providers_test() -> int:
    """`suijin providers --test` — one live request through each."""
    from rich.console import Console

    from suijin.modules.console.lib import settings_tui as st

    console = Console()
    from suijin.modules.providers.lib.registry import PROVIDER_REGISTRY

    console.print("[bold]Testing configured providers:[/bold]\n")
    any_ok = False
    for pid, spec in sorted(PROVIDER_REGISTRY.items()):
        key_s = st.provider_key_status(pid, {})
        if "✗" in key_s:
            console.print(f"  {pid:<16} [yellow]✗ no key[/yellow]")
            continue
        result = _test_one_provider(pid, {})
        ok = result.startswith("✓")
        any_ok = any_ok or ok
        color = "green" if ok else "red"
        console.print(f"  {pid:<16} [{color}]{result}[/{color}]")
    if not any_ok:
        console.print("\n[yellow]No providers responded — check keys with `suijin env`[/yellow]")
    return 0 if any_ok else 1


def _test_one_provider(provider: str, config: dict) -> str:
    """Send a 1-token request, time it, report the result."""
    import time

    try:
        from suijin.modules.providers.lib import generate

        cfg = dict(config or {})
        cfg["provider"] = provider
        model = str(cfg.get(f"{provider}_model") or "")
        if model:
            cfg["model"] = model
        cfg["max_tokens_per_request"] = 10
        start = time.time()
        resp = generate([{"role": "user", "content": "Say 'ok'"}], cfg)
        elapsed = time.time() - start
        if isinstance(resp, str) and resp.startswith("Error:"):
            return f"✗ {resp[:80]}"
        return f"✓ {elapsed:.1f}s"
    except Exception as e:  # noqa: BLE001 — the test never crashes
        return f"✗ {type(e).__name__}: {str(e)[:60]}"
