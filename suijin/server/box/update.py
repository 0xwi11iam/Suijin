"""box update — bring the box's suijin itself current (2026-11-01).

The box mounts the host repo (code is live), but the box's INSTALL —
the pip package the `suijin` launcher inside the box resolves — and the
engagement workspace state are separate surfaces. `box update`:
  1. repo: confirm the mount is current (or copy the tree when unmounted)
  2. package: pip install -U the mounted tree inside the box
  3. workspace: the standard refresh (config/env/packs/skills/tools)
  4. version stamp: print host version vs box version — drift is visible
"""

from __future__ import annotations


def cmd_update(args=None) -> int:
    import json

    def _print(msg: str = "") -> None:
        print(msg, flush=True)

    def _say(msg: str = "", style: str = "") -> None:
        _print(msg)

    from suijin.server.box import engine as bx
    from suijin.server.box.provision import stage_tools
    from suijin.server.box.sync import drift as compute_drift
    from suijin.server.box.sync import sync_selected

    if not bx.box_exists():
        _print("box not built — run `suijin box build` first")
        return 1
    if not bx.box_running():
        ok, out = bx.box_start()
        if not ok:
            _print(f"box failed to start: {out}")
            return 1
        _print("box started")

    host_repo = str(bx.repo_root()) if hasattr(bx, "repo_root") else None

    # 1. the box's view of the repo + its version stamp
    code, out = bx.run_in_box(
        "python3 -c \"import json;print(json.load(open('/Users/williamjiang/suijin/suijin/version.json'))['version'])\" 2>/dev/null || echo unknown",
        timeout=60,
    )
    box_ver = out.strip().splitlines()[-1] if out.strip() else "unknown"
    try:
        with open("/Users/williamjiang/suijin/suijin/version.json") as fh:
            host_ver = json.load(fh).get("version", "?")
    except Exception:  # noqa: BLE001
        host_ver = "?"
    _print(f"versions — host: {host_ver} · box: {box_ver}")

    # 2. package: reinstall from the mounted tree (pip resolves the mount)
    if host_repo:
        _print("updating the pip install inside the box …")
        code, out = bx.run_in_box(
            f"cd {host_repo} && (pip install -q -U -e . --break-system-packages 2>&1 || pip3 install -q -U -e . --break-system-packages 2>&1) | tail -2",
            timeout=600,
        )
        _print(out.strip().splitlines()[-1] if out.strip() else "pip: done" if code == 0 else f"pip: exit {code}")
    else:
        _print("repo root not resolvable — skipping package step")

    # 3. the standard refresh (config/env/packs/skills + missing tools)
    d = compute_drift()
    if d["changed"]:
        done = sync_selected(d["changed"], log=_print)
        _print(f"synced: {', '.join(done) if done else 'nothing'}")
    if d["missing_tools"]:
        _print(f"installing {len(d['missing_tools'])} missing tools …")
        stage_tools(log=_print)

    _say("box updated — host {} · box {}".format(host_ver, box_ver), "green bold")
    return 0
