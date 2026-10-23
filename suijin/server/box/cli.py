"""box cli — `suijin box …` verbs, headless-friendly.

suijin box                the manager tui (interactive)
suijin box build          create + provision (resumable)
suijin box start          ensure running
suijin box enter          drift check → interactive session (menu or shell)
suijin box shell          straight into an interactive bash shell in the box
suijin box status         one-line state
suijin box refresh        host → box sync (all items)
suijin box stop / destroy
"""

from __future__ import annotations

import contextlib


def _print(msg: str = "") -> None:
    print(msg, flush=True)


def _say(msg: str = "", style: str = "") -> None:
    """Bold-colored CLI output: ok=green, x=red, !=yellow, stage=bold."""
    try:
        from rich.console import Console as _C

        _C(stderr=False).print(msg, style=style) if style else _C().print(msg)
    except Exception:  # noqa: BLE001 — styling never breaks output
        _print(msg)


def cmd_status(args=None) -> int:
    from suijin.server.box import engine as bx
    from suijin.server.box.sync import drift as compute_drift

    eng = bx.detect_engine()
    if not eng:
        _print("no container engine found — install orbstack or docker")
        return 1
    exists = bx.box_exists(eng)
    running = bx.box_running(eng) if exists else False
    repo = bx.repo_root() or "?"
    _print(f"engine   {eng}")
    _print(f"box      {'running' if running else ('stopped' if exists else 'not built')}")
    _print(f"code     {repo} (live mount when built)")
    _print(f"data     {bx.BOX_WS}")
    if running:
        d = compute_drift()
        bits = []
        if d["changed"]:
            bits.append(f"drift: {', '.join(d['changed'])}")
        if d["missing_tools"]:
            bits.append(f"{len(d['missing_tools'])} tools pending")
        _print(f"sync     {'; '.join(bits) if bits else 'in sync'}")
        _print("sync     box → host sealed (one-way by design)")
    return 0


def cmd_build(args=None) -> int:
    from suijin.server.box import provision

    def log(m):
        _print(f"  {m}")

    summary = provision.build(log=log)
    if summary.get("ok"):
        _print(
            f"box ready · tools {summary.get('tools_ok', 0)}/{summary.get('tools_total', 0)} "
            "· enter with: suijin box enter"
        )
        return 0
    _print(f"build incomplete: {summary.get('error', 'see log above')} — re-run `suijin box build` to resume")
    return 1


def cmd_start(args=None) -> int:
    from suijin.server.box import engine as bx

    if not bx.box_exists():
        _print("box not built — run `suijin box build` first")
        return 1
    ok, out = bx.box_start()
    _say("box running", "green bold") if ok else _say(f"start failed: {out}", "red bold")
    return 0 if ok else 1


def cmd_stop(args=None) -> int:
    from suijin.server.box import engine as bx

    ok, out = bx.box_stop()
    _say("box stopped (data kept)", "green bold") if ok else _say(f"stop failed: {out}", "red bold")
    return 0 if ok else 1


def cmd_enter(args=None) -> int:
    """Drift check, then hand the terminal to the box."""
    from suijin.server.box import engine as bx
    from suijin.server.box.sync import drift as compute_drift

    if not bx.box_exists():
        _print("box not built — run `suijin box build` first")
        return 1
    if not bx.box_running():
        ok, out = bx.box_start()
        if not ok:
            _print(f"box failed to start: {out}")
            return 1
        _print("box started")
    d = compute_drift()
    action = "shell"
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        action = str((load_config() or {}).get("container", {}).get("enter_action") or "suijin")
    if d["changed"] or d["missing_tools"]:
        _print("host changed since last sync:")
        for c in d["changed"]:
            _print(f"  ~ {c}")
        if d["missing_tools"]:
            _print(f"  ! {len(d['missing_tools'])} tools in the host list not installed here")
        _print("run `suijin box refresh` to sync + install, then enter (or just enter now)")
    return bx.box_enter_interactive(action=action)


def cmd_refresh(args=None) -> int:
    from suijin.server.box import engine as bx
    from suijin.server.box.provision import stage_tools
    from suijin.server.box.sync import drift as compute_drift
    from suijin.server.box.sync import sync_selected

    if not bx.box_exists():
        _print("box not built")
        return 1
    if not bx.box_running():
        bx.box_start()
    d = compute_drift()
    if not (d["changed"] or d["missing_tools"]):
        _say("in sync — nothing to do", "green")
        return 0
    if d["changed"]:
        done = sync_selected(d["changed"], log=_print)
        _print(f"synced: {', '.join(done) if done else 'nothing'}")
    if d["missing_tools"]:
        _print(f"installing {len(d['missing_tools'])} missing tools …")
        stage_tools(log=_print)
    _say("refresh complete", "green bold")
    return 0


def cmd_destroy(args=None) -> int:
    from suijin.server.box import engine as bx

    if not bx.box_exists():
        _say("no box to destroy", "dim")
        return 0
    archive = "--no-archive" not in (args or [])
    if archive:
        _say("archiving engagements to ~/suijin-box-archive-<date>.tar.gz …", "bold")
        if not archive_box(_say):
            _say("archive failed — destroy aborted (the box is untouched)", "red bold")
            return 1
    ok, out = bx.box_delete()
    if ok:
        _say("box destroyed" + (" (archive kept)" if archive else ""), "green bold")
    else:
        _say(f"destroy failed: {out}", "red bold")
    return 0 if ok else 1


def archive_box(log=None) -> bool:
    """Stream the box workspace out as a tarball on the host — with a
    size estimate, live progress, and errors that actually print (the
    silent-hang post-mortem)."""
    import os
    import subprocess

    from suijin.server.box import engine as bx

    say = log or _print
    if not bx.box_running():
        bx.box_start()
    total_mb = 0
    with contextlib.suppress(Exception):
        rc, out = bx.run_in_box(f"du -sm {bx.BOX_WS} 2>/dev/null | cut -f1", timeout=60)
        if rc == 0 and out.strip().isdigit():
            total_mb = int(out.strip())
    date = __import__("time").strftime("%Y-%m-%d")
    dest = os.path.expanduser(f"~/suijin-box-archive-{date}.tar.gz")
    eng = bx.detect_engine()
    pull_cmd = ["orb", "-m", bx.BOX_NAME, "bash", "-lc", "tar czf - -C /opt suijin-ws"]
    if eng == bx.ENGINE_DOCKER:
        pull_cmd = ["docker", "exec", bx.BOX_NAME, "bash", "-lc", "tar czf - -C /opt suijin-ws"]
    try:
        pull = subprocess.Popen(pull_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        wrote = 0
        with open(dest, "wb") as fh:
            assert pull.stdout is not None
            while True:
                chunk = pull.stdout.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                wrote += len(chunk)
                mb = wrote >> 20
                if mb and mb % 32 == 0:
                    if total_mb:
                        say(f"  archive {mb} mb (~{100 * mb // max(1, total_mb)}% of {total_mb} mb)")
                    else:
                        say(f"  archive {mb} mb")
        rc = pull.wait(timeout=60)
        if rc != 0 or wrote == 0:
            say(f"  archive failed (rc={rc}, {wrote} bytes) — removing the partial file")
            with contextlib.suppress(OSError):
                os.unlink(dest)
            return False
        say(f"  archive complete: {dest} ({wrote >> 20} mb)")
        return True
    except Exception as e:  # noqa: BLE001
        say(f"  archive error: {e}")
        with contextlib.suppress(OSError):
            os.unlink(dest)
        return False


def cmd_shell(args=None) -> int:
    """`suijin box shell` — straight into an interactive bash shell in
    the box. No drift ceremony (that's `enter`'s job): this is the 'let
    me poke around' door. Starts the box if needed."""
    from suijin.server.box import engine as bx

    if not bx.box_exists():
        _print("box not built — run `suijin box build` first")
        return 1
    if not bx.box_running():
        ok, out = bx.box_start()
        if not ok:
            _print(f"box failed to start: {out}")
            return 1
        _print("box started")
    return bx.box_enter_interactive(action="shell")


VERBS = {
    "build": cmd_build,
    "start": cmd_start,
    "stop": cmd_stop,
    "enter": cmd_enter,
    "shell": cmd_shell,
    "status": cmd_status,
    "refresh": cmd_refresh,
    "destroy": cmd_destroy,
}


def run_box_cli(argv: list) -> int:
    """Entry: `suijin box <verb>` — no verb opens the tui."""
    if not argv:
        from suijin.server.box.tui import run_tui

        return run_tui()
    verb = str(argv[0]).strip().lower()
    fn = VERBS.get(verb)
    if fn is None:
        _print(f"unknown box command: {verb} (one of: {', '.join(sorted(VERBS))} or none for the tui)")
        return 2
    return fn(argv[1:])
