"""box engine — orbstack first, docker fallback, overridable.

Detection order (first match wins):
  1. SUIJIN_CONTAINER_ENGINE env var      (system-level override)
  2. config.json -> container.engine
  3. auto-detect: orb on PATH -> ORB, else docker on PATH -> DOCKER

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

BOX_NAME = "suijin-box"
BOX_WS = "/opt/suijin-ws"
BOX_STATE = f"{BOX_WS}/.box-state.json"
SYNC_MANIFEST = f"{BOX_WS}/.sync-manifest.json"
PROFILE_DROPIN = "/etc/profile.d/suijin-box.sh"

ENGINE_ORB = "orb"
ENGINE_DOCKER = "docker"


def detect_engine() -> str:
    """The resolved engine for this host."""
    v = str(os.environ.get("SUIJIN_CONTAINER_ENGINE") or "").strip().lower()
    if v in (ENGINE_ORB, ENGINE_DOCKER):
        return v
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        c = str((load_config() or {}).get("container", {}).get("engine") or "").strip().lower()
        if c in (ENGINE_ORB, ENGINE_DOCKER):
            return c
    if shutil.which("orb"):
        return ENGINE_ORB
    if shutil.which("docker"):
        return ENGINE_DOCKER
    return ""


def _engine_binary(engine: str) -> str:
    return engine if shutil.which(engine) else ""


def _run(cmd: list, timeout: int = 600) -> tuple[int, str]:
    """Run a host-side runtime command; never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def box_exists(engine: str | None = None) -> bool:
    eng = _engine_binary(engine or detect_engine())
    if not eng:
        return False
    if eng == ENGINE_ORB:
        # `orb list` shows running machines only — a stopped machine
        # still answers a direct exec (orb auto-starts on use), which
        # is the honest existence probe
        rc, _ = _run(["orb", "-m", BOX_NAME, "true"], timeout=120)
        return rc == 0
    rc, out = _run(["docker", "ps", "-a", "--format", "{{.Names}}"], timeout=30)
    return BOX_NAME in out


def box_running(engine: str | None = None) -> bool:
    eng = _engine_binary(engine or detect_engine())
    if not eng or not box_exists(eng):
        return False
    if eng == ENGINE_ORB:
        rc, out = _run(["orb", "list"], timeout=30)
        for ln in out.splitlines():
            parts = ln.split()
            if parts and parts[0] == BOX_NAME:
                return len(parts) > 1 and parts[1] == "running"
        return False
    rc, out = _run(["docker", "inspect", "-f", "{{.State.Running}}", BOX_NAME], timeout=30)
    return "true" in out.lower()


def box_create(engine: str | None = None) -> tuple[bool, str]:
    eng = _engine_binary(engine or detect_engine())
    if not eng:
        return False, "no container engine found (install orbstack or docker)"
    if eng == ENGINE_ORB:
        # orb takes a DISTRO name, not a docker image ref. Resource caps
        # keep every user's box bounded: the kali base is already
        # headless (no desktop metapackage ever pulled), and the disk
        # cap prevents toolchain balloon — trim container.tools in
        # config.json for a smaller footprint.
        rc, out = _run(
            ["orb", "create", "--memory", "4G", "--cpus", "2", "--disk", "25G", "kali", BOX_NAME],
            timeout=1800,
        )
        return rc == 0, out.strip()[-400:]
    rc, out = _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            BOX_NAME,
            "-v",
            f"{repo_root()}:/opt/suijin",
            "kalilinux/kali-rolling",
            "sleep",
            "infinity",
        ],
        timeout=1800,
    )
    return rc == 0, out.strip()[-400:]


def box_start(engine: str | None = None) -> tuple[bool, str]:
    eng = _engine_binary(engine or detect_engine())
    if not eng:
        return False, "no engine"
    if eng == ENGINE_ORB:
        if box_running(eng):
            return True, "already running"
        rc, out = _run(["orb", "start", BOX_NAME], timeout=300)
        return rc == 0, out.strip()[-200:]
    rc, out = _run(["docker", "start", BOX_NAME], timeout=300)
    return rc == 0, out.strip()[-200:]


def box_stop(engine: str | None = None) -> tuple[bool, str]:
    eng = _engine_binary(engine or detect_engine())
    if not eng:
        return False, "no engine"
    if eng == ENGINE_ORB:
        rc, out = _run(["orb", "stop", BOX_NAME], timeout=300)
        return rc == 0, out.strip()[-200:]
    rc, out = _run(["docker", "stop", BOX_NAME], timeout=300)
    return rc == 0, out.strip()[-200:]


def box_delete(engine: str | None = None) -> tuple[bool, str]:
    eng = _engine_binary(engine or detect_engine())
    if not eng:
        return False, "no engine"
    if eng == ENGINE_ORB:
        # -f: orb delete prompts for confirmation on running machines
        # — an invisible prompt on a pipe hangs forever (same class as
        # the apt -y bug)
        rc, out = _run(["orb", "delete", "-f", BOX_NAME], timeout=300)
        return rc == 0, out.strip()[-200:]
    rc, out = _run(["docker", "rm", "-f", BOX_NAME], timeout=300)
    return rc == 0, out.strip()[-200:]


def run_in_box(cmd: str, timeout: int = 3600, engine: str | None = None) -> tuple[int, str]:
    """Run a shell command inside the box. Explicit paths only."""
    eng = _engine_binary(engine or detect_engine())
    if not eng:
        return 1, "no engine"
    if eng == ENGINE_ORB:
        return _run(["orb", "-m", BOX_NAME, "bash", "-lc", cmd], timeout=timeout)
    return _run(["docker", "exec", BOX_NAME, "bash", "-lc", cmd], timeout=timeout)


def box_cmd_argv(cmd: str, engine: str | None = None) -> list:
    """The raw host-side argv for a box command (for streaming callers
    that need a Popen, not a captured result)."""
    eng = _engine_binary(engine or detect_engine())
    if eng == ENGINE_DOCKER:
        return ["docker", "exec", BOX_NAME, "bash", "-lc", cmd]
    return ["orb", "-m", BOX_NAME, "bash", "-lc", cmd]


def box_enter_interactive(action: str = "suijin", engine: str | None = None) -> int:
    """Replace this process with an interactive session in the box.
    action: 'suijin' (the menu) or 'shell'."""
    eng = _engine_binary(engine or detect_engine())
    if not eng:
        print("no container engine found")
        return 1
    cmd = "suijin" if action == "suijin" else "bash"
    argv = ["orb", "-m", BOX_NAME, cmd] if eng == ENGINE_ORB else ["docker", "exec", "-it", BOX_NAME, cmd]
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
