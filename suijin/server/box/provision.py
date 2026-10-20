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
    """The install line for a tool (kali). BOX ORDER (2026-10-20, the
    amass/go debugging): apt first; when the table has no apt entry
    (macOS-centric brew/go rows), try the GENERIC kali package before
    falling to pip/go — ffuf/nuclei/subfinder/katana all ship as kali
    packages but the doctor table only carries their go lines, and go
    lines fail instantly in a box without a go toolchain."""
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.availability import _INSTALL

        entry = _INSTALL.get(name) or {}
        apt = entry.get("apt")
        if apt:
            line = apt
            if "--no-install-recommends" not in line:
                line = line.replace("apt install", "apt install --no-install-recommends", 1)
            return line
        for key in ("pip", "go"):
            if entry.get(key):
                # generic kali package FIRST (most go-only tools ship in
                # kali); pip/go lines are the fallback, not the default
                return f"sudo apt install -y --no-install-recommends {name} || {entry[key]}"
    return f"sudo apt install -y --no-install-recommends {name}"


#: one tool may take this long before we kill it and move on (the
#: aircrack lesson: silent 40-minute timeouts are unacceptable)
_TOOL_TIMEOUT_S = 900
_IDLE_HEARTBEAT_S = 45


def _network_ok(log) -> bool:
    """Pre-toolchain connectivity probe — fail LOUDLY instead of 40
    minutes of per-tool timeouts when the box can't reach mirrors."""
    rc, out = bx.run_in_box("curl -sI --max-time 10 http://http.kali.org/kali | head -1", timeout=30)
    if rc == 0 and "HTTP/" in out:
        log(f"network: mirrors reachable ({out.strip()[:40]})")
        return True
    log(f"network: the box CANNOT reach package mirrors — skipping the toolchain ({out.strip()[:80]})")
    log("check the box's connectivity (orb networking / dns) and re-run `suijin box build`")
    return False


def _stream_install(cmd: str, name: str, log) -> tuple[bool, str]:
    """Stream the installer's own output live (the amass incident: a
    100MB package downloading invisibly reads as a hang). Idle
    heartbeat when quiet; killed at the cap with the tail shown."""
    import subprocess
    import threading

    argv = bx.box_cmd_argv(f"set -o pipefail; DEBIAN_FRONTEND=noninteractive {cmd}")
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    except Exception as e:  # noqa: BLE001
        return False, f"spawn failed: {e}"
    lines: list[str] = []
    last_activity = [time.time()]

    def _pump():
        try:
            for ln in proc.stdout or []:
                ln = ln.rstrip()
                if ln:
                    lines.append(ln)
                    last_activity[0] = time.time()
                    log(f"    {ln[:160]}")  # live — the operator watches apt work
        finally:
            pass

    threading.Thread(target=_pump, daemon=True).start()
    t0 = time.time()
    while proc.poll() is None:
        if time.time() - t0 > _TOOL_TIMEOUT_S:
            log(f"  {name} exceeded {_TOOL_TIMEOUT_S}s — killing and moving on")
            with contextlib.suppress(Exception):
                proc.kill()
            return False, "\n".join(lines[-6:])
        if time.time() - last_activity[0] >= _IDLE_HEARTBEAT_S:
            log(f"  still installing {name} ({int(time.time() - t0)}s, no new output) …")
            last_activity[0] = time.time()  # reset so the heartbeat paces itself
        time.sleep(2)
    with contextlib.suppress(Exception):
        proc.wait(timeout=30)
    return proc.returncode == 0, "\n".join(lines[-6:])


def ensure_ws() -> bool:
    """Create the box workspace owned by the box user (orb's default
    user is unprivileged; /opt needs sudo once, then it's ours)."""
    rc, _ = bx.run_in_box("sudo mkdir -p /opt/suijin-ws && sudo chown -R $(whoami) /opt/suijin-ws", timeout=60)
    return rc == 0


def stage_base(log) -> bool:
    log("installing base packages (python, pipx, git …)")
    # pipefail: a piped tail must never mask the install's exit code;
    # sudo: the orb default user is unprivileged with passwordless sudo
    rc, out = bx.run_in_box(
        "set -o pipefail; sudo apt-get update -qq && "
        "DEBIAN_FRONTEND=noninteractive sudo apt-get install -y -qq --no-install-recommends "
        + " ".join(_BASE_PACKAGES)
        + " 2>&1 | tail -3",
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


# ── PREREQ-DECLARING INSTALL PLAN (2026-10-20, operator ruling: tools
# declare what they need BEFORE anything runs — no one-at-a-time
# discovery). Probe what the box can apt-install, choose each tool's
# line, collect the RUNTIMES those choices require (go/gem), install
# the runtimes first, then the tools — every step visible.
_RUNTIME_PKGS = {"go": "golang-go", "gem": "ruby"}


def _probe_apt_candidates(tools: list[str]) -> set[str]:
    """Which tools have a kali apt candidate (one batched probe)."""
    if not tools:
        return set()
    rc, out = bx.run_in_box(
        "for t in " + " ".join(tools) + "; do apt-cache policy $t 2>/dev/null | grep -q 'Candidate:' && echo $t; done",
        timeout=600,
    )
    return {ln.strip() for ln in out.splitlines() if ln.strip()} if rc == 0 else set()


def _resolve_plan(tools: list[str]) -> dict[str, tuple[str, str]]:
    """tool -> (runtime, install line). The doctor table's apt line
    wins; else the generic kali package when a candidate exists; else
    the table's pip/go/gem fallback (runtime declared); else a generic
    apt attempt (runtime 'apt' — always present in the box)."""
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.availability import _INSTALL

        table = _INSTALL
    apt_ok = _probe_apt_candidates([t for t in tools if not (table.get(t) or {}).get("apt")])
    plan: dict[str, tuple[str, str]] = {}
    for t in tools:
        entry = table.get(t) or {}
        apt = entry.get("apt")
        if apt:
            line = apt
            if "--no-install-recommends" not in line:
                line = line.replace("apt install", "apt install --no-install-recommends", 1)
            plan[t] = ("apt", line)
        elif t in apt_ok:
            plan[t] = ("apt", f"sudo apt install -y --no-install-recommends {t}")
        else:
            for rt, prefix in (("pip", "pip install"), ("go", "go install"), ("gem", "gem install")):
                line = entry.get(rt)
                if line:
                    plan[t] = (rt, line)
                    break
            else:
                plan[t] = ("apt", f"sudo apt install -y --no-install-recommends {t}")
    return plan


def _runtimes_for(plan: dict[str, tuple[str, str]]) -> set[str]:
    """The non-apt runtimes the plan requires (prereqs to install
    BEFORE the tools that need them)."""
    return {rt for rt, _ in plan.values() if rt in _RUNTIME_PKGS}


def stage_tools(log, progress=None, cancel=None) -> bool:
    """Install the toolchain per the PREREQ-DECLARING plan: probe →
    plan (visible summary) → runtimes → tools. Live output, visible
    results, network precheck, dpkg-lock repair, presence decides."""
    tools = tool_list()
    state = _state_read()
    done_tools = state.setdefault("tools", {})
    todo = [t for t in tools if not done_tools.get(t, {}).get("ok")]
    total, done_count = len(tools), len(tools) - len(todo)
    log(f"toolchain: {total - len(todo)}/{total} present, {len(todo)} to install")
    if todo and not _network_ok(log):
        return False  # loud skip — no point timing out per-tool
    with contextlib.suppress(Exception):
        bx.run_in_box(
            "sudo rm -f /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock 2>/dev/null; "
            "sudo dpkg --configure -a >/dev/null 2>&1; true",
            timeout=300,
        )
    plan = _resolve_plan(todo)
    by_rt: dict[str, int] = {}
    for rt, _ in plan.values():
        by_rt[rt] = by_rt.get(rt, 0) + 1
    log("plan: " + " · ".join(f"{n} via {rt}" for rt, n in sorted(by_rt.items())))
    # PREREQS FIRST: runtimes the plan depends on, installed visibly
    for rt in sorted(_runtimes_for(plan)):
        pkg = _RUNTIME_PKGS[rt]
        rc_chk, _ = bx.run_in_box(f"command -v {'go' if rt == 'go' else 'gem'}", timeout=30)
        if rc_chk != 0:
            log(
                f"prereq: {len([1 for r, _ in plan.values() if r == rt])} tools need the {rt} runtime — installing {pkg}"
            )
            ok, tail = _stream_install(f"sudo apt install -y --no-install-recommends {pkg}", pkg, log)
            if not ok:
                log(f"prereq {pkg} failed — the {rt} tools will be skipped this round:")
                for ln in (tail or "").splitlines()[-3:]:
                    log(f"    {ln}")
                plan = {t: v for t, v in plan.items() if v[0] != rt}
    for name in todo:
        if name not in plan:
            continue  # dropped: its runtime prereq failed
        if cancel and cancel():
            log("paused — re-run build to continue")
            return False
        rt, cmd = plan[name]
        log(f"installing {name} (via {rt}): {cmd}")
        _ok, tail = _stream_install(cmd, name, log)
        # PRESENCE is the truth (installer exit codes lie both ways)
        chk_rc, _ = bx.run_in_box(f"command -v {name}", timeout=30)
        present = chk_rc == 0
        done_tools[name] = {"ok": present, "via": rt, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        done_count += 1
        if present:
            log(f"  ok {name}")
        else:
            log(f"  x {name} — not present after install; last output:")
            for ln in (tail or "no output").splitlines()[-4:]:
                log(f"    {ln}")
        if progress:
            progress(done_count, total, name, present, "" if present else (tail or "")[-120:])
        _state_write(state)  # resumable per tool
    return True


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
