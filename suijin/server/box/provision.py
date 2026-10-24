"""box provision — the build stages, resumable.

State lives on the box (/opt/suijin-ws/.box-state.json): every finished
stage and tool is recorded, so a canceled or failed build resumes where
it stopped — never from zero. Tool installs come from the declarative
tool manifest (tool_manifest.py: platforms, prereqs, ordered fallback
chains), idempotent by nature (skip-if-present re-check per tool).

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
    # the toolchain's manifest lines are sudo-prefixed; root-only docker
    # images (kalilinux) ship without the binary — install it as root so
    # every later line works identically on every engine
    "sudo",
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
    """The install line for a tool (kali) — the manifest's first line,
    box-safe. Used by the TUI's manual per-tool install. Built-ins get
    a no-op (nothing to install)."""
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.tool_manifest import install_plan

        plan = install_plan(name, "kali")
        return _box_line(plan[0]) if plan else "true"
    return f"sudo apt install -y --no-install-recommends {name}"


def _box_line(line: str) -> str:
    """Make a manifest fallback executable as the unprivileged box user.
    pip needs --user + --break-system-packages (PEP 668), gem needs sudo
    (the system gem dir); apt lines arrive already automation-safe — and
    run as apt-get in PIPE MODE: the fancy `apt` front-end draws \\r-based
    progress bars that shred the live stream into diagonal scattered
    rows when the runtime allocates a pty (field 2026-10-24)."""
    line = line.split(" (", 1)[0].strip()  # drop trailing prose/parentheticals
    if line.startswith(("pip install ", "pip3 install ")):
        tools = line.split()
        rest = tools[2:]
        flags = [f for f in ("--user", "--break-system-packages") if f not in rest]
        return " ".join([tools[0], "install", *flags, *rest])
    if line.startswith("gem install "):
        return f"sudo {line}"
    # apt -> apt-get, quiet, dpkg without its pty progress redraws —
    # every engine (orb's exec gives a pty; docker exec doesn't) gets
    # the same clean line output
    if " apt install " in f" {line} " or line.endswith(" apt install"):
        line = line.replace("apt install", "apt-get install -q -o Dpkg::Use-Pty=0")
    if " apt update" in line:
        line = line.replace("apt update", "apt-get update -q")
    return line


def _clean_stream_line(ln: str) -> str:
    """One clean row per streamed line: \r-redraws collapse to their
    FINAL state, control chars drop, and the padding redraws leave
    behind collapses — the operator sees straight rows (2026-10-24)."""
    import re

    ln = ln.split("\r")[-1]
    ln = re.sub(r"[\x00-\x08\x0b-\x1f\x1b]", "", ln)
    ln = re.sub(r" {4,}", "   ", ln)
    return ln.strip()


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
    heartbeat when quiet; killed at the cap with the tail shown. PATH
    carries the go/pip-user install dirs so fallback chains can run."""
    import subprocess
    import threading

    argv = bx.box_cmd_argv(
        f"set -o pipefail; export PATH=$HOME/google-cloud-sdk/bin:$HOME/go/bin:$HOME/.local/bin:$PATH; DEBIAN_FRONTEND=noninteractive {cmd}"
    )
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    except Exception as e:  # noqa: BLE001
        return False, f"spawn failed: {e}"
    lines: list[str] = []
    last_activity = [time.time()]

    def _pump():
        try:
            for ln in proc.stdout or []:
                ln = _clean_stream_line(ln)
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
    user is unprivileged; docker's kali image runs as root without a
    sudo binary — $S covers both)."""
    rc, _ = bx.run_in_box(
        "S=''; command -v sudo >/dev/null 2>&1 && S=sudo; "
        f"$S mkdir -p {bx.BOX_WS} && $S chown -R $(whoami) {bx.BOX_WS}",
        timeout=60,
    )
    return rc == 0


def stage_base(log) -> bool:
    log("installing base packages (python, pipx, git …)")
    # pipefail: a piped tail must never mask the install's exit code.
    # sudo is OPTIONAL: orb's default user is unprivileged (passwordless
    # sudo), but docker's kalilinux image runs as ROOT with no sudo
    # binary — `$S` is empty there and the bootstrap still works
    # (full-compatibility rule 2026-10-24: same path on every engine).
    rc, out = bx.run_in_box(
        "set -o pipefail; S=''; command -v sudo >/dev/null 2>&1 && S=sudo; "
        "$S apt-get update -qq && "
        "DEBIAN_FRONTEND=noninteractive $S apt-get install -y -qq -o Dpkg::Use-Pty=0 --no-install-recommends "
        + " ".join(_BASE_PACKAGES)
        + " 2>&1 | tail -3",
        timeout=1800,
    )
    if rc != 0:
        log(f"base packages failed: {out.strip()[-200:]}")
        return False
    return True


def _stream_repo_into_box(log) -> bool:
    """Tar-stream the source tree into /opt/suijin (adopted containers
    get no live mount — docker can't add -v to an existing container).
    Excludes carry real weight: .venv is 170MB+, and agent-vault holds
    local-only secrets that must NEVER cross into any container."""
    import subprocess

    repo = bx.repo_root()
    if not repo:
        return False
    excludes = [
        "--exclude=.venv",
        "--exclude=.git",
        "--exclude=agent-vault",
        "--exclude=__pycache__",
        "--exclude=.pytest_cache",
        "--exclude=.ruff_cache",
        "--exclude=node_modules",
        "--exclude=*.egg-info",
    ]
    try:
        tar = subprocess.run(["tar", "czf", "-", *excludes, "-C", repo, "."], capture_output=True, timeout=600)
        if tar.returncode != 0:
            log(f"repo tar failed: {tar.stderr.decode()[-160:]}")
            return False
        proc = subprocess.run(
            bx.box_cmd_argv(
                "mkdir -p /opt/suijin && tar xzf - --warning=no-unknown-keyword -C /opt/suijin",
                stdin_attached=True,
            ),
            input=tar.stdout,
            capture_output=True,
            timeout=600,
        )
        if proc.returncode != 0:
            log(f"repo extract failed: {proc.stderr.decode()[-160:]}")
            return False
        log(f"streamed the source tree into /opt/suijin ({len(tar.stdout) >> 20}MB) — no live mount on this container")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"repo stream failed: {e}")
        return False


def stage_suijin(log) -> bool:
    repo = bx.repo_root()
    if not repo:
        log("cannot locate the suijin source tree on the host")
        return False
    log(f"editable install from {repo}")
    rc, _ = bx.run_in_box(f"test -f {repo}/pyproject.toml", timeout=30)
    inner = repo if rc == 0 else ""
    if not inner:
        # no live mount (adopted container, or a mount that vanished):
        # refresh /opt/suijin from the host tree, then install from there
        if not _stream_repo_into_box(log):
            return False
        inner = "/opt/suijin"
    rc, out = bx.run_in_box(
        "set -o pipefail; export PATH=$HOME/.local/bin:$PATH; "
        "if test -d ~/.local/pipx/venvs/suijin; then "
        f"cd {inner} && ~/.local/pipx/venvs/suijin/bin/pip install --editable . --no-deps -q 2>&1 | tail -2; "
        "else "
        f"pipx install --editable {inner} --pip-args=--no-cache-dir 2>&1 | tail -3; fi",
        timeout=2400,
    )
    if rc != 0:
        log(f"pipx install failed: {out.strip()[-200:]}")
        return False
    return True


def _stage_resumable(stage: str) -> bool:
    """profile/config re-run every build (the host→box contract, cheap and
    secret-bearing); the heavy stages resume from box state."""
    return stage not in ("profile", "config")


def stage_profile(log) -> bool:
    log("writing the shell profile (workspace + path)")
    profile = (
        f"export SUIJIN_WORKSPACE={bx.BOX_WS}\n"
        # the box workspace IS the config source (host: ~/.suijin/config).
        # Without these the loader fell back to the package dir — which in
        # the box is the live-mounted host repo, so it read the repo .env
        # (no provider key) and wrote model choices INTO the host tree.
        f"export SUIJIN_CONFIG={bx.BOX_WS}/config.json\n"
        f"export SUIJIN_ENV={bx.BOX_WS}/.env\n"
        "export PATH=$HOME/.local/bin:$HOME/go/bin:$HOME/google-cloud-sdk/bin:/usr/local/sbin:/usr/sbin:/sbin:$PATH\n"
    )
    quoted = profile.replace("'", "'\\''")
    rc, _ = bx.run_in_box(
        f"printf '%s' '{quoted}' | sudo tee {bx.PROFILE_DROPIN} >/dev/null && sudo chmod 644 {bx.PROFILE_DROPIN}",
        timeout=60,
    )
    # PATH for EVERY shell, not just login shells: profile.d covers
    # `bash -l` (all our exec paths), .bashrc covers the interactive
    # `box enter` shell — installed tools must answer `command -v` in
    # both (operator ruling 2026-10-24: make sure it adds tools to path)
    rc2, _ = bx.run_in_box(
        "grep -q 'suijin-box path' ~/.bashrc 2>/dev/null || "
        "printf '%s\\n' '# suijin-box path (tools on PATH in every shell)' "
        "'export PATH=$HOME/.local/bin:$HOME/go/bin:$HOME/google-cloud-sdk/bin:/usr/local/sbin:/usr/sbin:/sbin:$PATH' "
        ">> ~/.bashrc",
        timeout=60,
    )
    return rc == 0 and rc2 == 0


def stage_config(log) -> bool:
    log("syncing host config → box (packs, prompts, config, .env)")
    from suijin.server.box.sync import _SYNC_ITEMS

    done = sync_selected(list(_SYNC_ITEMS), log=log)
    log(f"synced: {', '.join(done) if done else 'nothing new'}")
    return True  # best-effort: partial sync still boots


# ── PREREQ-DECLARING INSTALL PLAN (2026-10-20, operator ruling: tools
# declare what they need BEFORE anything runs — no one-at-a-time
# discovery). The manifest (tool_manifest.py) supplies every tool's
# platform matrix, prereqs and ordered fallback chain; one batched apt
# probe decides whether the apt line or a declared fallback applies.
# Runtimes are installed BEFORE the tools — every step visible.
_RUNTIME_PKGS = {"go": "golang-go", "gem": "ruby", "pip": "python3-pip"}
_RUNTIME_BIN = {"go": "go", "gem": "gem", "pip": "pip3"}


def _apt_package(line: str) -> str | None:
    """The package an apt line installs ('sudo apt install -y ldap-utils'
    -> 'ldap-utils') — the probe must ask about the PACKAGE, not the
    binary name (ldapsearch/searchsploit/smbclient all differ)."""
    toks = line.replace("sudo ", "", 1).split()
    with contextlib.suppress(ValueError):
        idx = toks.index("install")
        for tok in toks[idx + 1 :]:
            if not tok.startswith("-"):
                return tok
    return None


def _probe_apt_candidates(packages: dict[str, str]) -> set[str] | None:
    """Which tools have a kali apt candidate (one batched probe).
    ``packages`` maps tool -> package name. None means the probe itself
    failed — no information, trust the manifest instead of mass
    falling back."""
    if not packages:
        return set()
    pairs = " ".join(f"{t}:{pkg}" for t, pkg in packages.items() if pkg)
    if not pairs.strip():
        return set()
    rc, out = bx.run_in_box(
        "for pair in " + pairs + '; do t="${pair%%:*}"; p="${pair#*:}"; '
        'apt-cache show "$p" >/dev/null 2>&1 && echo "$t"; done',
        timeout=600,
    )
    if rc != 0:
        return None
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def _resolve_plan(tools: list[str]) -> dict[str, tuple[str, str]]:
    """tool -> (runtime, install line) from the DECLARATIVE MANIFEST.

    Chain line 1 first (apt when the tool ships a kali package), then
    the manifest's declared fallbacks (go/pip/gem) — each carrying its
    declared runtime. One batched apt probe (by PACKAGE name) refines
    the apt choice; a failed probe trusts the manifest."""
    from suijin.modules.tools.lib import tool_manifest as tm

    chains = {t: tm.install_plan(t, "kali") for t in tools}
    apt_lines = {t: next((ln for ln in chains[t] if "apt install" in ln), None) for t in tools}
    packages = {t: _apt_package(ln) for t, ln in apt_lines.items() if ln}
    apt_ok = _probe_apt_candidates(packages)  # None = probe unavailable
    plan: dict[str, tuple[str, str]] = {}
    for t in tools:
        chain = chains[t]
        if not chain:
            rec = tm.manifest().get(t) or {}
            # known built-in → nothing to run; known tool with no chain
            # declared for kali (e.g. a macOS-only CLI) → say so, don't
            # pretend it is built in
            plan[t] = ("builtin", "true") if rec.get("built_in") else ("unsupported", "")
            continue
        apt_line = apt_lines[t]
        fallbacks = [ln for ln in chain if "apt install" not in ln]
        if apt_line and (apt_ok is None or t in apt_ok):
            plan[t] = ("apt", apt_line)
        elif fallbacks:
            raw = fallbacks[0]
            plan[t] = (tm._line_runtime(raw) or "shell", _box_line(raw))
        elif apt_line:
            plan[t] = ("apt", apt_line)  # last resort — presence decides
        else:
            plan[t] = ("apt", f"sudo apt install -y --no-install-recommends {t}")
    return plan


def _runtimes_for(plan: dict[str, tuple[str, str]]) -> set[str]:
    """Runtimes the plan requires that this stage can install — from
    the manifest-declared runtime of each chosen line (never discovered
    by failure)."""
    return {rt for rt, _ in plan.values() if _RUNTIME_PKGS.get(rt)}


def presence_script(tools: list[str]) -> str:
    """One shell script reporting each tool's DECLARED presence
    ('<tool> ok' / '<tool> missing') — any acceptable binary OR a
    python module. Single box round-trip for the TUI tools screen."""
    from suijin.modules.tools.lib.tool_manifest import verify_checks

    parts = []
    for name in tools:
        cmds = [
            f'python3 -c "import {value}"' if kind == "python" else f"command -v {value}"
            for kind, value in verify_checks(name)
        ]
        joined = " || ".join(cmds) if cmds else f"command -v {name}"
        parts.append(f'({joined}) >/dev/null 2>&1 && echo "{name} ok" || echo "{name} missing"')
    return "; ".join(parts)


def _present(name: str) -> bool:
    """Presence per the manifest's DECLARED checks: any acceptable
    binary (nxc OR crackmapexec) or a python module (requests has no
    binary). Installer exit codes lie both ways — this decides."""
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.tool_manifest import verify_checks

        cmds = [
            f'python3 -c "import {value}"' if kind == "python" else f"command -v {value}"
            for kind, value in verify_checks(name)
        ]
        rc, _ = bx.run_in_box(" || ".join(cmds) if cmds else f"command -v {name}", timeout=30)
        return rc == 0
    rc, _ = bx.run_in_box(f"command -v {name}", timeout=30)
    return rc == 0


def _fallback_attempts(name: str, chosen: str) -> list[tuple[str, str]]:
    """Chain lines AFTER the chosen one — genuine fall-through. apt
    lines are skipped (the probe already decided apt); box-safe
    transforms apply; the chosen line is not repeated."""
    out: list[tuple[str, str]] = []
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.tool_manifest import _line_runtime, install_plan

        seen = {chosen}
        for line in install_plan(name, "kali"):
            if "apt install" in line:
                continue
            boxed = _box_line(line)
            if boxed in seen:
                continue
            seen.add(boxed)
            out.append((_line_runtime(line) or "shell", boxed))
    return out


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
    if plan:
        log("plan: " + " · ".join(f"{n} via {rt}" for rt, n in sorted(by_rt.items())))
    # PREREQS FIRST: runtimes the plan depends on, installed visibly
    for rt in sorted(_runtimes_for(plan)):
        pkg = _RUNTIME_PKGS[rt]
        rc_chk, _ = bx.run_in_box(f"command -v {_RUNTIME_BIN.get(rt, rt)}", timeout=30)
        if rc_chk != 0:
            log(
                f"prereq: {len([1 for r, _ in plan.values() if r == rt])} tools need the {rt} runtime — installing {pkg}"
            )
            ok, tail = _stream_install(_box_line(f"sudo apt install -y --no-install-recommends {pkg}"), pkg, log)
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
        if progress:
            progress(done_count, total, name, None, "installing …")  # in-flight: the bar breathes
        rt, cmd = plan[name]
        tail = ""
        via = rt
        if rt == "builtin":
            log(f"checking {name} (built in / nothing to install)")
            present = _present(name)
        elif rt == "unsupported":
            from suijin.modules.tools.lib.tool_manifest import platforms_of

            plats = ", ".join(platforms_of(name)) or "none declared"
            log(f"skipping {name} — no install path declared for kali (platforms: {plats})")
            present = _present(name)
        else:
            # ORDERED FALL-THROUGH: the chosen line first, then every
            # declared fallback — stop as soon as presence passes
            attempts = [(rt, cmd)] + _fallback_attempts(name, cmd)
            present = False
            for art, acmd in attempts:
                log(f"installing {name} (via {art}): {acmd}")
                _ok, tail = _stream_install(acmd, name, log)
                present = _present(name)
                via = art
                if present:
                    break
        # DECLARED presence is the truth (installer exit codes lie both ways)
        done_tools[name] = {"ok": present, "via": via, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        with contextlib.suppress(Exception):
            from suijin.modules.tools.lib.tool_manifest import verify_checks

            done_tools[name]["check"] = ",".join(":".join(c) for c in verify_checks(name))
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


def build(resume: bool = True, log=print, progress=None, cancel=None, stage_cb=None) -> dict:
    """Run all stages. Returns a summary {ok, stages, tools_ok,
    tools_total}. Resumes from recorded state when resume=True.
    `stage_cb(name, ok)` fires as each stage COMPLETES — the tui colors
    its rows from these (done=green) instead of guessing."""

    def _stage_done(name: str, ok: bool) -> None:
        with contextlib.suppress(Exception):
            if stage_cb:
                stage_cb(name, ok)

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
        # profile/config are the host→box contract and cheap to redo:
        # skipping them on resume left existing boxes on a stale profile
        # (the 2026-10-22 field crash: no SUIJIN_CONFIG/SUIJIN_ENV, so the
        # box read the mounted repo's config instead of its own).
        if _stage_resumable(stage) and state["stages"].get(stage, {}).get("ok"):
            log(f"stage {stage}: already done (resumed)")
            _stage_done(stage, True)  # resumed counts as done — green row
            continue
        ok = runners[stage](log)
        state["stages"][stage] = {"ok": ok, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _state_write(state)
        _stage_done(stage, ok)
        if not ok:
            return {"ok": False, "error": f"stage {stage} failed (re-run to resume)", "stages": state["stages"]}
    # ALWAYS reconcile: per-tool state is the source of truth, and a
    # previous run may have recorded the stage flag without the tool
    # records (or lost them). Complete boxes finish this in one no-op
    # read; incomplete ones resume per tool.
    ok = stage_tools(log, progress=progress, cancel=cancel)
    state = _state_read()  # stage_tools persisted per-tool records
    state["stages"]["tools"] = {"ok": ok}
    _state_write(state)
    _stage_done("tools", ok)
    if not ok and not (cancel and cancel()):
        pass  # paused, not failed — summary reflects partial
    ok = stage_verify(log)
    state["stages"]["verify"] = {"ok": ok}
    _state_write(state)
    _stage_done("verify", ok)
    tools_state = _state_read().get("tools", {})
    current = tool_list()
    return {
        "ok": ok,
        "stages": state["stages"],
        # count only the CURRENT list — stale records from an earlier
        # container.tools config must not inflate the summary
        "tools_ok": sum(1 for t in current if tools_state.get(t, {}).get("ok")),
        "tools_total": len(current),
    }
