"""box provision — the build stages, resumable.

State lives on the box (/opt/suijin-ws/.box-state.json): every finished
stage and tool is recorded, so a canceled or failed build resumes where
it stopped — never from zero. Tool installs use the doctor's apt lines
(kali), idempotent by nature (skip-if-present re-check per tool).

Stage order:
  1 base      system packages (python, pipx, git, tar, curl)
  2 suijin    editable pipx install from the live-mounted repo
  3 profile   SUIJIN_WORKSPACE + PATH drop-in for every shell
  4 config    host → box sync (packs, prompts, config, .env)
  5 tools     the security toolchain from container.tools
  6 verify    first-boot check (suijin --version, doctor count)
"""

from __future__ import annotations

import contextlib
import json
import time

from suijin.server.box import engine as bx
from suijin.server.box.sync import sync_selected, tool_list

STAGES = ("base", "suijin", "profile", "config", "tools", "verify")

_BASE_PACKAGES = [
    "python3",
    "python3-pip",
    "pipx",
    "git",
    "curl",
    "tar",
    "ca-certificates",
]


def _state_read() -> dict:
    rc, out = bx.run_in_box(f"cat {bx.BOX_STATE} 2>/dev/null", timeout=60)
    with contextlib.suppress(Exception):
        if rc == 0 and out.strip():
            return json.loads(out)
    return {"stages": {}, "tools": {}}


def _state_write(state: dict) -> None:
    body = json.dumps(state).replace("'", "'\\''")
    bx.run_in_box(f"mkdir -p {bx.BOX_WS} && printf '%s' '{body}' > {bx.BOX_STATE}", timeout=60)


def _tool_apt_line(name: str) -> str:
    """The apt install line for a tool (kali). The doctor's table is
    the source; a miss falls back to the obvious apt name."""
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.availability import _INSTALL

        entry = _INSTALL.get(name) or {}
        for key in ("apt", "pip", "go"):
            if entry.get(key):
                return entry[key]
    return f"sudo apt install -y {name}"


def stage_base(log) -> bool:
    log("installing base packages (python, pipx, git …)")
    # pipefail: a piped tail must never mask the install's exit code
    # (the first smoke run passed a broken stage this way). sudo: the
    # orb default user is unprivileged with passwordless sudo.
    rc, out = bx.run_in_box(
        "set -o pipefail; sudo apt-get update -qq && "
        "DEBIAN_FRONTEND=noninteractive sudo apt-get install -y -qq " + " ".join(_BASE_PACKAGES) + " 2>&1 | tail -3",
        timeout=1800,
    )
    if rc != 0:
        log(f"base packages failed: {out.strip()[-200:]}")
        return False
    return True


def stage_suijin(log) -> bool:
    repo = bx.repo_root()
    if not repo:
        log("cannot locate the suijin source tree on the host")
        return False
    log(f"editable install from {repo}")
    # ORB mounts the host path at the same absolute location; DOCKER
    # mounts it at /opt/suijin. Detect which one the box sees.
    rc, _ = bx.run_in_box(f"test -f {repo}/pyproject.toml", timeout=30)
    inner = repo if rc == 0 else "/opt/suijin"
    rc, out = bx.run_in_box(
        "set -o pipefail; export PATH=$HOME/.local/bin:$PATH; "
        f"pipx install --editable {inner} --pip-args=--no-cache-dir 2>&1 | tail -3",
        timeout=2400,
    )
    if rc != 0:
        log(f"pipx install failed: {out.strip()[-200:]}")
        return False
    return True


def stage_profile(log) -> bool:
    log("writing the shell profile (workspace + path)")
    profile = "export SUIJIN_WORKSPACE=/opt/suijin-ws\nexport PATH=$HOME/.local/bin:$PATH\n"
    quoted = profile.replace("'", "'\\''")
    rc, _ = bx.run_in_box(
        f"printf '%s' '{quoted}' | sudo tee {bx.PROFILE_DROPIN} >/dev/null && sudo chmod 644 {bx.PROFILE_DROPIN}",
        timeout=60,
    )
    return rc == 0


def stage_config(log) -> bool:
    log("syncing host config → box (packs, prompts, config, .env)")
    done = sync_selected(
        ["config.json", "packs", "skills", "prompts", "prompt.md", "SOUL.md", "drive_policy.json", "profiles"],
        log=log,
    )
    log(f"synced: {', '.join(done) if done else 'nothing new'}")
    return True  # best-effort: partial sync still boots


def stage_tools(log, progress=None, cancel=None) -> bool:
    """Install the toolchain. progress(done, total, name, ok, note) for
    the live UI; cancel() -> True aborts after the current tool."""
    tools = tool_list()
    state = _state_read()
    done_tools = state.setdefault("tools", {})
    todo = [t for t in tools if not done_tools.get(t, {}).get("ok")]
    total, done_count = len(tools), len(tools) - len(todo)
    log(f"toolchain: {total - len(todo)}/{total} present, {len(todo)} to install")
    for name in todo:
        if cancel and cancel():
            log("paused — re-run build to continue")
            return False
        cmd = _tool_apt_line(name)
        log(f"installing {name}: {cmd}")
        rc, out = bx.run_in_box(f"set -o pipefail; DEBIAN_FRONTEND=noninteractive {cmd} 2>&1 | tail -2", timeout=2400)
        # PRESENCE is the truth (the smoke run proved installer exit
        # codes lie through pipes): an installer may no-op for a
        # preinstalled tool or "fail" after succeeding — command -v
        # decides, and a miss downgrades ok regardless of rc
        chk_rc, _ = bx.run_in_box(f"command -v {name}", timeout=30)
        ok = chk_rc == 0
        done_tools[name] = {"ok": ok, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        done_count += 1
        if progress:
            progress(done_count, total, name, ok, out.strip()[-120:] if not ok else "")
        _state_write(state)  # resumable per tool
    return True


def ensure_ws() -> bool:
    """Create the box workspace owned by the box user (orb's default
    user is unprivileged; /opt needs sudo once, then it's ours)."""
    rc, _ = bx.run_in_box("sudo mkdir -p /opt/suijin-ws && sudo chown -R $(whoami) /opt/suijin-ws", timeout=60)
    return rc == 0


def stage_verify(log) -> bool:
    rc, out = bx.run_in_box(
        "set -o pipefail; export PATH=$HOME/.local/bin:$PATH; command -v suijin >/dev/null && suijin --version 2>&1 | head -1",
        timeout=120,
    )
    if rc != 0 or "suijin" not in out.lower():
        log(f"verify failed: suijin not callable inside the box ({out.strip()[:120]})")
        return False
    log(f"verified: {out.strip()}")
    return True


def build(resume: bool = True, log=print, progress=None, cancel=None) -> dict:
    """Run all stages. Returns a summary {ok, stages, tools_ok,
    tools_total}. Resumes from recorded state when resume=True."""
    if not bx.box_exists():
        log("creating the box (kali) — first boot may take a few minutes")
        ok, out = bx.box_create()
        if not ok:
            return {"ok": False, "error": f"box create failed: {out}"}
    if not bx.box_running():
        bx.box_start()
    ensure_ws()
    state = _state_read() if resume else {"stages": {}, "tools": {}}
    runners = {
        "base": stage_base,
        "suijin": stage_suijin,
        "profile": stage_profile,
        "config": stage_config,
    }
    for stage in STAGES:
        if stage in ("tools", "verify"):
            continue  # tools runs with progress below; verify runs last
        if state["stages"].get(stage, {}).get("ok"):
            log(f"stage {stage}: already done (resumed)")
            continue
        ok = runners[stage](log)
        state["stages"][stage] = {"ok": ok, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _state_write(state)
        if not ok:
            return {"ok": False, "error": f"stage {stage} failed (re-run to resume)", "stages": state["stages"]}
    if not state["stages"].get("tools", {}).get("ok"):
        ok = stage_tools(log, progress=progress, cancel=cancel)
        state["stages"]["tools"] = {"ok": ok}
        _state_write(state)
        if not ok and not (cancel and cancel()):
            pass  # paused, not failed — summary reflects partial
    ok = stage_verify(log)
    state["stages"]["verify"] = {"ok": ok}
    _state_write(state)
    tools_state = _state_read().get("tools", {})
    return {
        "ok": ok,
        "stages": state["stages"],
        "tools_ok": sum(1 for v in tools_state.values() if v.get("ok")),
        "tools_total": len(tool_list()),
    }
