"""box cli — `suijin box …` verbs, headless-friendly.

suijin box                the manager tui (interactive)
suijin box build          create + provision (resumable)
suijin box start          ensure running
suijin box enter          drift check → interactive session
suijin box status         one-line state
suijin box refresh        host → box sync (all items)
suijin box stop / destroy
"""

from __future__ import annotations

import contextlib


def _print(msg: str = "") -> None:
    print(msg, flush=True)


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
    _print("box running" if ok else f"start failed: {out}")
    return 0 if ok else 1


def cmd_stop(args=None) -> int:
    from suijin.server.box import engine as bx

    ok, out = bx.box_stop()
    _print("box stopped (data kept)" if ok else f"stop failed: {out}")
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
        _print("in sync — nothing to do")
        return 0
    if d["changed"]:
        done = sync_selected(d["changed"], log=_print)
        _print(f"synced: {', '.join(done) if done else 'nothing'}")
    if d["missing_tools"]:
        _print(f"installing {len(d['missing_tools'])} missing tools …")
        stage_tools(log=_print)
    _print("refresh complete")
    return 0


def cmd_destroy(args=None) -> int:
    from suijin.server.box import engine as bx

    if not bx.box_exists():
        _print("no box to destroy")
        return 0
    archive = "--no-archive" not in (args or [])
    if archive:
        _print("archiving engagements to ~/suijin-box-archive-<date>.tar.gz …")
        ok = archive_box()
        if not ok:
            _print("archive failed — destroy aborted (box untouched)")
            return 1
    ok, out = bx.box_delete()
    _print("box destroyed" + (" (archive kept)" if archive else "") if ok else f"destroy failed: {out}")
    return 0 if ok else 1


def archive_box() -> bool:
    """Stream the box workspace out as a tarball on the host."""
    import os
    import subprocess

    from suijin.server.box import engine as bx

    if not bx.box_running():
        bx.box_start()
    date = __import__("time").strftime("%Y-%m-%d")
    dest = os.path.expanduser(f"~/suijin-box-archive-{date}.tar.gz")
    eng = bx.detect_engine()
    try:
        if eng == bx.ENGINE_ORB:
            pull = subprocess.Popen(
                ["orb", "-m", bx.BOX_NAME, "bash", "-lc", "tar czf - -C /opt suijin-ws"], stdout=subprocess.PIPE
            )
        else:
            pull = subprocess.Popen(
                ["docker", "exec", bx.BOX_NAME, "bash", "-lc", "tar czf - -C /opt suijin-ws"], stdout=subprocess.PIPE
            )
        with open(dest, "wb") as fh:
            assert pull.stdout is not None
            while True:
                chunk = pull.stdout.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
        return pull.wait() == 0
    except Exception:  # noqa: BLE001
        return False


VERBS = {
    "build": cmd_build,
    "start": cmd_start,
    "stop": cmd_stop,
    "enter": cmd_enter,
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
