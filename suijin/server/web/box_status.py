"""Boxes and the warm pile, as the console sees them.

Two different things are called a box:

* **Sandboxes** — the hypervisor's disposable agent containers, ``suijin-sbx-<engagement>-<identity>``.
* **The warm pile** — sandbox containers that were created and never started, held ready so the next
  boxed agent starts from a pre-created container: ``suijin-sbx-pool-*``.

Docker is slow (every call re-probes the daemon) and the first call can wake OrbStack, so nothing
here runs on a request path. :func:`snapshot` returns the last state and refreshes it in a
background thread; :func:`refresh_now` is the same work, inline, for tests and for the Refresh
button. The engine is only probed when its socket already exists.
"""

from __future__ import annotations

import contextlib
import threading
import time

REFRESH_S = 8.0
FRESHNESS_S = 60.0
IMAGE_NAME = "suijin/sandbox-base"
POOL_PREFIX = "suijin-sbx-pool"
SBX_PREFIX = "suijin-sbx"

_state: dict = {"at": 0.0, "data": None, "refreshing": False, "freshness_at": 0.0, "fresh": None}
_lock = threading.Lock()

_build: dict = {"state": "idle", "started_at": None, "finished_at": None, "log": [], "error": "", "force": False}
_build_lock = threading.Lock()


def _engine_socket() -> str:
    """The hypervisor's own answer to "is an engine already listening?" — a file lookup, so
    asking for box state can never start OrbStack."""
    from suijin.server.box.hypervisor import engine_socket

    return engine_socket()


def classify(name: str) -> dict:
    """What a sandbox container's name says about it: pooled or an agent's, and whose."""
    if name.startswith(POOL_PREFIX):
        return {"pooled": True, "engagement": "", "identity": ""}
    rest = name[len(SBX_PREFIX) + 1 :] if name.startswith(SBX_PREFIX + "-") else name
    # Both halves contain hyphens (an engagement slug; an identity such as agent-1490-ab09), so the
    # name is split at the identity's own marker, never at its last hyphen.
    for marker in ("-agent-", "-m1-leader-", "-m2-sup-", "-m3-worker-"):
        at = rest.rfind(marker)
        if at > 0:
            return {"pooled": False, "engagement": rest[:at], "identity": rest[at + 1 :]}
    engagement, _, identity = rest.rpartition("-")
    return {"pooled": False, "engagement": engagement, "identity": identity}


def _collect() -> dict:
    """The real state: one engine probe, three listings."""
    started = time.time()
    out: dict = {
        "engine": "not running",
        "engine_socket": "",
        "sandboxes": [],
        "pool": {"size": 0, "target": 0, "max": 32},
        "image": {"name": IMAGE_NAME, "tag": "", "exists": False, "fresh": None},
        "box": {"engine": "", "name": "", "adopted": False, "state": "unknown", "status": ""},
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "took_ms": 0,
    }
    from suijin.server.platform.lib.config_loader import load_config

    with contextlib.suppress(Exception):
        container = dict((load_config() or {}).get("container") or {})
        out["box"]["engine"] = str(container.get("engine") or "")
        out["box"]["name"] = str(container.get("name") or "suijin-box")
        out["box"]["adopted"] = bool(container.get("name"))

    socket = _engine_socket()
    out["engine_socket"] = socket
    if not socket:
        out["box"]["state"] = "engine off"
        out["took_ms"] = int((time.time() - started) * 1000)
        return out

    from suijin.server.box import hypervisor as hyp

    try:
        if not hyp.engine_available():
            out["box"]["state"] = "engine off"
            return out
        out["engine"] = "running"
        out["pool"]["target"] = hyp.pool_target()
        pool_members: list[dict] = []
        for row in hyp.sandbox_ls():
            status = str(row.get("status") or "")
            item = {
                "name": row["name"],
                "status": status,
                "age": str(row.get("age") or ""),
                "running": "up" in status.lower(),
                **classify(row["name"]),
            }
            (pool_members if item["pooled"] else out["sandboxes"]).append(item)
        out["pool"]["members"] = pool_members
        out["pool"]["size"] = len(pool_members)
        out["image"]["exists"] = hyp.image_exists()
        out["image"]["tag"] = hyp.image_tag()
        if time.time() - _state["freshness_at"] > FRESHNESS_S or _state["fresh"] is None:
            _state["fresh"] = bool(out["image"]["exists"] and hyp.image_is_fresh())
            _state["freshness_at"] = time.time()
        out["image"]["fresh"] = _state["fresh"]
        # the persistent box: a container when the engine is docker, an OrbStack machine when it is orb
        rc, listing = hyp._docker(
            "ps", "-a", "--filter", f"name=^{out['box']['name']}$", "--format", "{{.Names}}\t{{.Status}}", timeout=15
        )
        if rc == 0 and listing.strip():
            status = listing.split("\t", 1)[-1].strip()
            out["box"]["status"] = status
            out["box"]["state"] = "running" if status.lower().startswith("up") else "stopped"
        else:
            # A docker box is a container of this name. An OrbStack machine is not a container, and
            # inspecting it would mean running the orb CLI, which can wake OrbStack: say so.
            orb = out["box"]["engine"] == "orb"
            out["box"]["state"] = "not checked" if orb else "not built"
            out["box"]["note"] = (
                "an OrbStack machine; docker does not list it" if orb else 'run "suijin box build" to create it'
            )
    except Exception as error:  # noqa: BLE001 — the console shows the reason, never a 500
        out["error"] = f"{type(error).__name__}: {str(error)[:200]}"
    out["took_ms"] = int((time.time() - started) * 1000)
    return out


def _refresh() -> None:
    with _lock:
        if _state["refreshing"]:
            return
        _state["refreshing"] = True
    try:
        data = _collect()
        with _lock:
            _state["data"] = data
            _state["at"] = time.time()
    except Exception as error:  # noqa: BLE001
        with _lock:
            _state["error"] = f"{type(error).__name__}: {str(error)[:200]}"
            _state["at"] = time.time()
    finally:
        with _lock:
            _state["refreshing"] = False


def refresh_now() -> dict:
    """Collect inline and return the new state."""
    _refresh()
    return dict(_state["data"] or {})


def snapshot() -> dict:
    """The last known state; stale state is refreshed in the background, never waited for."""
    with _lock:
        age = time.time() - _state["at"] if _state["data"] is not None else None
        stale = age is None or age > REFRESH_S
        busy = _state["refreshing"]
        data = dict(_state["data"] or {})
    if stale and not busy:
        threading.Thread(target=_refresh, daemon=True, name="box-status").start()
    data["stale"] = bool(stale)
    data["loading"] = _state["data"] is None
    return data


# ── the template image ─────────────────────────────────────────────────────────────────────


def image_build_state() -> dict:
    with _build_lock:
        return {key: (list(value) if isinstance(value, list) else value) for key, value in _build.items()}


def start_image_build(force: bool = False) -> dict:
    """Build the sandbox template image in the background; the log is kept for the console."""
    with _build_lock:
        if _build["state"] == "running":
            return {**image_build_state(), "started": False}
        _build.update(
            {"state": "running", "started_at": time.time(), "finished_at": None, "log": [], "error": "", "force": force}
        )

    def run() -> None:
        from suijin.server.box import hypervisor as hyp

        def on_log(line) -> None:
            with _build_lock:
                _build["log"] = (_build["log"] + [str(line)[:400]])[-200:]

        try:
            tag = hyp.build_image(force=force, on_log=on_log)
            with _build_lock:
                _build.update({"state": "done", "finished_at": time.time(), "tag": tag})
        except Exception as error:  # noqa: BLE001
            on_log(f"build failed: {error}")
            with _build_lock:
                _build.update(
                    {
                        "state": "failed",
                        "finished_at": time.time(),
                        "error": f"{type(error).__name__}: {str(error)[:300]}",
                    }
                )
        refresh_now()

    threading.Thread(target=run, daemon=True, name="box-image-build").start()
    return {**image_build_state(), "started": True}
