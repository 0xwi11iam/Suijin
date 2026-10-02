"""The lab supervisor — boots, resets, tears down, and reports.

`suijin lab up` boots all five listening services + the worker; a fresh
internal token is minted per boot; DBs/buckets/queue re-seed on reset.
Deterministic: same seed state, same run. `status` health-checks every
service; `telemetry` replays the engagement's chain-edge events.
"""

from __future__ import annotations

import json
import os
import secrets as _secrets
import signal
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

def _base() -> int:
    """PORT_BASE resolved at CALL time: a test fixture may set the env
    AFTER this package was first imported — a cached module constant
    health-checked the wrong ports while the services booted right."""
    import os as _os

    return int(_os.environ.get("NB_PORT_BASE", "6000"))


from suijin.lab.northbridge import PORT_BASE  # subprocesses: fresh import reads env
from suijin.lab.northbridge import (
    CHAIN_EDGES,
    CROWN_FLAGS,
    PORT_ADMIN,
    PORT_AUTH,
    PORT_CORE,
    PORT_EDGE,
    PORT_OBJECTS,
    ROOT,
    TIER_FLAGS,
    emit,
    telemetry_dir,
)

#: ports resolve at CALL time (see _base): a cached constant breaks any
#: boot under a different NB_PORT_BASE (bench offset, test bases)
SERVICES = {
    "edge": ("suijin.lab.northbridge.edge", 0),
    "auth": ("suijin.lab.northbridge.auth", 1),
    "core": ("suijin.lab.northbridge.core", 2),
    "objects": ("suijin.lab.northbridge.objects", 3),
    "admin": ("suijin.lab.northbridge.admin", 5),
}


def _ports() -> dict:
    return {n: (mod, _base() + off) for n, (mod, off) in SERVICES.items()}


def _generated_services() -> dict:
    """The catalog fleet — fifteen more services, each its own process."""
    from suijin.lab.northbridge import catalog

    return {
        spec["name"]: (f"suijin.lab.northbridge.gen.{spec['name']}", _base() + spec["off"])
        for spec in catalog.SERVICES
    }


def _all_services() -> dict:
    return {**_ports(), **_generated_services()}
PID_FILE = os.path.join(ROOT, "supervisor.json")


def _mint_token() -> None:
    os.makedirs(ROOT, exist_ok=True)
    tok = _secrets.token_hex(24)
    with open(os.path.join(ROOT, "internal-token"), "w", encoding="utf-8") as f:
        f.write(tok)
    os.chmod(os.path.join(ROOT, "internal-token"), 0o600)


def reset_state() -> None:
    """Wipe + reseed DBs, buckets, queue, exports, telemetry."""
    import shutil

    os.makedirs(ROOT, exist_ok=True)
    for sub in ("core.db", "auth.db", "buckets", "queue", "exports", "claimed", "telemetry", "mail", "worker-env.txt", "defense.json", "content"):
        p = os.path.join(ROOT, sub)
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.unlink(p)
    import glob as _glob

    for f in _glob.glob(os.path.join(ROOT, "svc-*.db")):
        os.unlink(f)
    # import seeders lazily so the module graph stays cheap for callers
    from suijin.lab.northbridge import auth as _auth
    from suijin.lab.northbridge import objects as _objects
    from suijin.lab.northbridge import worker as _worker
    from suijin.lab.northbridge import core as _core

    _mint_token()
    _auth.seed()
    _core.seed()
    _objects.seed()
    _worker.seed()
    emit("lab_reset", "fresh boot")


def _sweep_orphans() -> None:
    """Kill any lab process from a PREVIOUS boot still holding our ports
    (a crashed run wipes the PID file — its children survive and serve
    STALE code, which once made a routing fix look impossible). Only
    processes running OUR modules are touched."""
    import subprocess as _sp

    try:
        out = _sp.run(
            ["pgrep", "-f", "suijin.lab.northbridge"], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:  # noqa: BLE001
        return
    for pid_s in out.split():
        try:
            pid = int(pid_s)
            if pid != os.getpid():
                os.kill(pid, signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    time.sleep(0.4)


def up(reset: bool = True, timeout_s: float = 30.0) -> dict:
    """Boot the stack. Returns {service: port}. Idempotent per-process."""
    down()
    _sweep_orphans()
    if reset:
        reset_state()
    all_services = _all_services()
    procs = {}
    for name, (mod, _port) in all_services.items():
        # PYTHONPATH carries the repo root: the launcher runs cli.py as a
        # SCRIPT (sys.path[0] = its own dir), so `python -m suijin.lab...`
        # children cannot import the package from any cwd except the repo
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        _repo = str(Path(__file__).resolve().parents[3])
        env["PYTHONPATH"] = _repo + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        err_path = os.path.join(ROOT, f"boot-{name}.err")
        errf = open(err_path, "wb")  # owned by the child after spawn; swept at reset
        procs[name] = subprocess.Popen(
            [sys.executable, "-m", mod], env=env, stdout=subprocess.DEVNULL, stderr=errf
        )
        errf.close()
    procs["worker"] = subprocess.Popen(
        [sys.executable, "-m", "suijin.lab.northbridge.worker"],
        env=dict(os.environ, PYTHONUNBUFFERED="1"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.makedirs(ROOT, exist_ok=True)
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(json.dumps({k: p.pid for k, p in procs.items()}))
    deadline = time.time() + timeout_s
    pending = {n for n in all_services if n != "worker"}
    while pending and time.time() < deadline:
        for name in list(pending):
            port = all_services[name][1]
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                    if r.status == 200:
                        pending.discard(name)
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
    if pending:
        down()
        detail = ""
        for n in pending:
            ef = os.path.join(ROOT, f"boot-{n}.err")
            if os.path.isfile(ef):
                with open(ef, "rb") as fh:
                    tail = fh.read()[-160:].decode(errors="replace").replace("\n", " | ")
                detail += f" [{n}: {tail}]"
        raise RuntimeError(f"services failed to boot: {sorted(pending)}{detail}")
    return {n: all_services[n][1] for n in all_services} | {"worker": "queue"}


def down() -> None:
    if not os.path.isfile(PID_FILE):
        return
    pids = json.loads(open(PID_FILE, encoding="utf-8").read())
    for pid in pids.values():
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            pass
    os.unlink(PID_FILE)
    time.sleep(0.3)


def status() -> dict:
    out = {"running": [], "down": [], "telemetry_edges": 0}
    for name, (_, port) in _all_services().items():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                out["running" if r.status == 200 else "down"].append(f"{name}:{port}")
        except Exception:  # noqa: BLE001
            out["down"].append(f"{name}:{port}")
    ev = os.path.join(telemetry_dir(), "events.jsonl")
    if os.path.isfile(ev):
        out["telemetry_edges"] = sum(1 for _ in open(ev, encoding="utf-8"))
    return out


def telemetry(limit: int = 200) -> list[dict]:
    ev = os.path.join(telemetry_dir(), "events.jsonl")
    if not os.path.isfile(ev):
        return []
    lines = open(ev, encoding="utf-8").read().splitlines()[-limit:]
    return [json.loads(ln) for ln in lines if ln.strip()]


def manifest() -> dict:
    """The bench's contract: flags + the chain graph."""
    from suijin.lab.northbridge import catalog
    from suijin.lab.northbridge.catalog import flag_for

    gen_flags = []
    for spec in catalog.SERVICES:
        for pattern, target, params in spec.get("vulns", []):
            if pattern != "decoy_panel":
                gen_flags.append(flag_for(pattern, spec["name"], str(params)))
    return {
        "lab": "northbridge",
        "edge_port": PORT_EDGE,
        "services": 5 + len(catalog.SERVICES) + 1,
        "tier_flags": TIER_FLAGS,
        "generated_flags": gen_flags,
        "crown_flags": CROWN_FLAGS,
        "extended_chains": catalog.EXTENDED_CHAINS,
        "decoy_flag": "FLAG{northbridge_decoy_admin_bypass}",
        "decoy_validates_false": True,
        "chain_edges": CHAIN_EDGES,
    }


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "up":
        print(json.dumps(up()))
    elif cmd == "down":
        down()
        print("down")
    elif cmd == "reset":
        down()
        print(json.dumps(up(reset=True)))
    elif cmd == "telemetry":
        print(json.dumps(telemetry(), indent=1))
    else:
        print(json.dumps(status()))
