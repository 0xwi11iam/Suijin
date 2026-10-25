"""box engine — macOS: orbstack first; every other platform: docker.

Detection order (first match wins):
  1. SUIJIN_CONTAINER_ENGINE env var      (system-level override)
  2. config.json -> container.engine
  3. one-time interactive pick — ONLY on macOS with BOTH runtimes
     (orbstack is mac-only; a Linux user with docker is never asked)
  4. auto-detect: macOS -> orb, else docker

All box operations go through this module; nothing else shells to a
container runtime directly. Every command runs with explicit /opt
paths only — the standing container rule (no ~, no $HOME anywhere a
box executes).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess

BOX_WS = "/opt/suijin-ws"
BOX_STATE = f"{BOX_WS}/.box-state.json"
SYNC_MANIFEST = f"{BOX_WS}/.sync-manifest.json"
PROFILE_DROPIN = "/etc/profile.d/suijin-box.sh"

ENGINE_ORB = "orb"
ENGINE_DOCKER = "docker"

#: the managed box (created/owned/deleted by suijin). An ADOPTED
#: container — config `container.name`, set by `suijin box adopt` — is
#: the operator's own: suijin provisions INTO it and never deletes it.
DEFAULT_BOX_NAME = "suijin-box"
BOX_NAME = DEFAULT_BOX_NAME  # back-compat alias; prefer box_name()


def box_name() -> str:
    """The container every box verb operates on: config container.name
    (an adopted container) > the managed default. Validated lazily —
    a broken config never breaks the box layer."""
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        n = str((load_config() or {}).get("container", {}).get("name") or "").strip()
        if n:
            return n
    return DEFAULT_BOX_NAME


def is_adopted() -> bool:
    """True when suijin points at an operator-owned container."""
    return box_name() != DEFAULT_BOX_NAME


def _orb_available() -> bool:
    """OrbStack exists on macOS only — an `orb` binary on any other
    platform is not a usable engine (full-compatibility rule 2026-10-24:
    never assume the mac setup)."""
    import sys

    return sys.platform == "darwin" and bool(shutil.which("orb"))


def detect_engine(interactive: bool = True) -> str:
    """The resolved engine for this host.

    Order: SUIJIN_CONTAINER_ENGINE env > config container.engine >
    one-time interactive pick (macOS with BOTH runtimes and a TTY — the
    choice persists to config) > auto-detect (macOS: orb first; every
    other platform: docker). A persisted choice is never asked again;
    a Linux user with docker is NEVER asked."""
    v = str(os.environ.get("SUIJIN_CONTAINER_ENGINE") or "").strip().lower()
    if v in (ENGINE_ORB, ENGINE_DOCKER):
        return v
    configured = _configured_engine()
    if configured:
        return configured

    have_orb, have_docker = _orb_available(), bool(shutil.which("docker"))
    if have_orb and have_docker and interactive and _stdin_is_tty():
        choice = _prompt_engine()
        if choice in (ENGINE_ORB, ENGINE_DOCKER):
            _persist_engine(choice)
            return choice
    if have_orb:
        return ENGINE_ORB
    if have_docker:
        return ENGINE_DOCKER
    return ""


def _configured_engine() -> str:
    """The persisted engine choice, if any (config container.engine)."""
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        c = str((load_config() or {}).get("container", {}).get("engine") or "").strip().lower()
        if c in (ENGINE_ORB, ENGINE_DOCKER):
            return c
    return ""


def _stdin_is_tty() -> bool:
    import sys

    try:
        return sys.stdin.isatty()
    except Exception:  # noqa: BLE001
        return False


def _prompt_engine() -> str:

    try:
        print("both container runtimes are available:")
        print("  1) orbstack (orb) — mac-native, recommended on this device")
        print("  2) docker")
        raw = input("select [1/2, enter = 1]: ").strip()
        return ENGINE_DOCKER if raw == "2" else ENGINE_ORB
    except Exception:  # noqa: BLE001 — non-interactive contexts
        return ""


def _persist_engine(choice: str) -> None:
    """Remember the pick so it is asked exactly once."""
    with contextlib.suppress(Exception):
        import json
        from pathlib import Path

        from suijin.modules.platform.lib.config_loader import CONFIG_PATH

        p = Path(CONFIG_PATH)
        cfg = {}
        if p.exists():
            cfg = json.loads(p.read_text(encoding="utf-8") or "{}")
        cont = cfg.setdefault("container", {})
        cont["engine"] = choice
        p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _engine_binary(engine: str) -> str:
    return engine if shutil.which(engine) else ""


def _run(cmd: list, timeout: int = 600) -> tuple[int, str]:
    """Run a host-side runtime command; never raises. stdin is DEVNULL:
    an inherited stdin (a supervisor's open pipe — pytest, launchers,
    daemons) makes container CLIs block forever reading it (the
    operator-console test hang, 2026-10-26)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def docker_context_pin() -> str:
    """The docker context suijin PINS its docker calls to (config
    container.docker_context / SUIJIN_DOCKER_CONTEXT). With OrbStack
    installed its context hijacks the plain `docker` CLI — a pin makes
    every suijin docker call deterministic ( OrbStack can re-assert its
    global context all it wants; suijin still hits the pinned daemon).
    '' = follow the machine's current context."""
    import os as _os

    v = str(_os.environ.get("SUIJIN_DOCKER_CONTEXT") or "").strip()
    if v:
        return v
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        v = str((load_config() or {}).get("container", {}).get("docker_context") or "").strip()
        if v:
            return v
    return ""


def docker_argv(*args) -> list:
    """A docker command argv with the pinned context (if any) applied."""
    ctx = docker_context_pin()
    return ["docker", *(["--context", ctx] if ctx else []), *args]


def docker_daemon() -> str:
    """WHICH docker daemon suijin's docker calls resolve to: the pinned
    context first, then DOCKER_HOST, then the machine's current context
    ('orbstack', 'desktop-linux', …). Surfaced in status/TUI so nobody
    wonders why a 'docker' pick lands inside OrbStack (2026-10-24)."""
    import os as _os

    ctx = docker_context_pin()
    if ctx:
        return ctx
    host = str(_os.environ.get("DOCKER_HOST") or "").strip()
    if host:
        tail = host.rsplit("/", 1)[-1]
        return tail or host
    with contextlib.suppress(Exception):
        rc, out = _run(["docker", "context", "show"], timeout=15)
        c = (out or "").strip()
        if rc == 0 and c and c != "default":
            return c
    return ""


def box_exists(engine: str | None = None) -> bool:
    eng = _engine_binary(engine or detect_engine())
    if not eng:
        return False
    if eng == ENGINE_ORB:
        # `orb list` shows running machines only — a stopped machine
        # still answers a direct exec (orb auto-starts on use), which
        # is the honest existence probe
        rc, _ = _run(["orb", "-m", box_name(), "true"], timeout=120)
        return rc == 0
    rc, out = _run(docker_argv("ps", "-a", "--format", "{{.Names}}"), timeout=30)
    # EXACT name match: a substring test made "suijin-box" match
    # "suijin-box-old" (and any adopted name match its prefixes)
    return box_name() in (out or "").split()


def box_running(engine: str | None = None) -> bool:
    eng = _engine_binary(engine or detect_engine())
    if not eng or not box_exists(eng):
        return False
    if eng == ENGINE_ORB:
        rc, out = _run(["orb", "list"], timeout=30)
        for ln in out.splitlines():
            parts = ln.split()
            if parts and parts[0] == box_name():
                return len(parts) > 1 and parts[1] == "running"
        return False
    rc, out = _run(docker_argv("inspect", "-f", "{{.State.Running}}", box_name()), timeout=30)
    return "true" in out.lower()


def _docker_mount_path(host_path: str) -> str:
    """Docker Desktop (Windows hosts) wants /c/Users/... not C:\\Users\\...;
    posix hosts pass through untouched."""
    import sys

    if sys.platform == "win32" and len(host_path) > 2 and host_path[1] == ":":
        drive = host_path[0].lower()
        rest = host_path[2:].replace("\\", "/")
        return f"/{drive}{rest}"
    return host_path


def box_create(engine: str | None = None) -> tuple[bool, str]:
    eng = _engine_binary(engine or detect_engine())
    if not eng:
        return False, "no container engine found — install docker (or orbstack on macOS)"
    if is_adopted():
        # the operator OWNS this container — suijin never creates (or
        # deletes) it; `suijin box adopt <name>` provisioned INTO it
        return False, f"container.name is set to the adopted container {box_name()!r} — suijin does not create it"
    if eng == ENGINE_ORB:
        # orb takes a DISTRO name, not a docker image ref. Resource caps
        # keep every user's box bounded: the kali base is already
        # headless (no desktop metapackage ever pulled), and the disk
        # cap prevents toolchain balloon — trim container.tools in
        # config.json for a smaller footprint.
        rc, out = _run(
            ["orb", "create", "--memory", "4G", "--cpus", "2", "--disk", "25G", "kali", box_name()],
            timeout=1800,
        )
        return rc == 0, out.strip()[-400:]
    # docker (linux/windows/any): same resource posture as orb minus the
    # disk cap — `--storage-opt size=` only works on pquota-xfs hosts and
    # would fail creation outright everywhere else. The repo mount is
    # conditional: a pip-installed suijin has no tree to mount and the
    # build falls back cleanly instead of `docker run` dying on `-v ""`.
    argv = docker_argv(
        "run",
        "-d",
        "--name",
        box_name(),
        "--memory",
        "4g",
        "--cpus",
        "2",
    )
    root = repo_root()
    if root:
        argv += ["-v", f"{_docker_mount_path(root)}:/opt/suijin"]
    argv += ["kalilinux/kali-rolling", "sleep", "infinity"]
    rc, out = _run(argv, timeout=1800)
    if rc != 0 and root:
        # The daemon can't share this path — BOXING FROM INSIDE A
        # CONTAINER: mounts resolve against the DAEMON'S host (the
        # machine), where the inner container's repo path doesn't exist
        # ("mounts denied … not shared from the host"). Retry bare:
        # stage_suijin's tar-stream fallback carries the source in.
        _run(docker_argv("rm", "-f", box_name()), timeout=120)
        bare = [a for i, a in enumerate(argv) if not (a == "-v" or (i and argv[i - 1] == "-v"))]
        rc, out = _run(bare, timeout=1800)
    return rc == 0, out.strip()[-400:]


def box_start(engine: str | None = None) -> tuple[bool, str]:
    eng = _engine_binary(engine or detect_engine())
    if not eng:
        return False, "no engine"
    if eng == ENGINE_ORB:
        if box_running(eng):
            return True, "already running"
        rc, out = _run(["orb", "start", box_name()], timeout=300)
        return rc == 0, out.strip()[-200:]
    rc, out = _run(docker_argv("start", box_name()), timeout=300)
    return rc == 0, out.strip()[-200:]


def box_stop(engine: str | None = None) -> tuple[bool, str]:
    eng = _engine_binary(engine or detect_engine())
    if not eng:
        return False, "no engine"
    if eng == ENGINE_ORB:
        rc, out = _run(["orb", "stop", box_name()], timeout=300)
        return rc == 0, out.strip()[-200:]
    rc, out = _run(docker_argv("stop", box_name()), timeout=300)
    return rc == 0, out.strip()[-200:]


def box_delete(engine: str | None = None) -> tuple[bool, str]:
    eng = _engine_binary(engine or detect_engine())
    if not eng:
        return False, "no engine"
    if is_adopted():
        # ADOPTED containers are the operator's own: remove ONLY suijin's
        # footprint INSIDE them — never the container itself. Explicit
        # /opt paths only (the standing container rule).
        name = box_name()
        rc, out = run_in_box(
            "S=''; command -v sudo >/dev/null 2>&1 && S=sudo; "
            f"$S rm -rf {BOX_WS} /opt/suijin && $S rm -f {PROFILE_DROPIN}",
            timeout=300,
            engine=eng,
        )
        return (rc == 0), (
            f"suijin's footprint removed from {name} — the container itself is kept" if rc == 0 else out.strip()[-200:]
        )
    if eng == ENGINE_ORB:
        # -f: orb delete prompts for confirmation on running machines
        # — an invisible prompt on a pipe hangs forever (same class as
        # the apt -y bug)
        rc, out = _run(["orb", "delete", "-f", box_name()], timeout=300)
        return rc == 0, out.strip()[-200:]
    rc, out = _run(docker_argv("rm", "-f", box_name()), timeout=300)
    return rc == 0, out.strip()[-200:]


def run_in_box(cmd: str, timeout: int = 3600, engine: str | None = None) -> tuple[int, str]:
    """Run a shell command inside the box. Explicit paths only."""
    eng = _engine_binary(engine or detect_engine())
    if not eng:
        return 1, "no engine"
    if eng == ENGINE_ORB:
        return _run(["orb", "-m", box_name(), "bash", "-lc", cmd], timeout=timeout)
    return _run(docker_argv("exec", box_name(), "bash", "-lc", cmd), timeout=timeout)


def box_cmd_argv(cmd: str, engine: str | None = None, stdin_attached: bool = False) -> list:
    """The raw host-side argv for a box command (for streaming callers
    that need a Popen, not a captured result). `stdin_attached` adds -i
    on docker (tar streams); orb's exec is pty-based and always accepts
    stdin."""
    eng = _engine_binary(engine or detect_engine())
    if eng == ENGINE_DOCKER:
        return docker_argv("exec", *(["-i"] if stdin_attached else []), box_name(), "bash", "-lc", cmd)
    return ["orb", "-m", box_name(), "bash", "-lc", cmd]


def _enter_argv(action: str, eng: str) -> list:
    """The host argv for an interactive session. The suijin action goes
    through `bash -lc` on docker: `docker exec -it X suijin` resolves
    the binary against the IMAGE's default PATH, which does not carry
    ~/.local/bin (where pipx puts suijin) — a login shell loads the
    profile dropin and finds it. orb's exec already runs profiled."""
    cmd = "suijin" if action == "suijin" else "bash"
    if eng == ENGINE_ORB:
        return ["orb", "-m", box_name(), cmd]
    if cmd == "suijin":
        return docker_argv("exec", "-it", box_name(), "bash", "-lc", "suijin")
    return docker_argv("exec", "-it", box_name(), "bash")


def box_enter_interactive(action: str = "suijin", engine: str | None = None) -> int:
    """Replace this process with an interactive session in the box.
    action: 'suijin' (the menu) or 'shell'."""
    eng = _engine_binary(engine or detect_engine())
    if not eng:
        print("no container engine found")
        return 1
    argv = _enter_argv(action, eng)
    os.execvp(argv[0], argv)  # noqa: S606 — a real TTY handoff
    return 1


def repo_root() -> str:
    """The suijin source tree to live-mount (editable install)."""
    import suijin as _pkg

    here = _pkg.__file__
    if here:
        p = os.path.dirname(os.path.dirname(os.path.abspath(here)))
        if os.path.exists(os.path.join(p, "pyproject.toml")) or os.path.exists(os.path.join(p, ".git")):
            return p
    fallback = os.path.expanduser("~/.suijin/repo")
    if os.path.exists(os.path.join(fallback, "pyproject.toml")):
        return os.path.realpath(fallback)
    return ""
