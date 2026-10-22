"""box sync — one-way host → box config sync with drift detection.

The host workspace (config.json, packs, skills, prompts, prompt.md,
SOUL.md, drive_policy) is the source of truth. The box keeps a
snapshot manifest of what it last received (.sync-manifest.json);
`drift()` compares the live host against that snapshot.

ONE-WAY BY DESIGN: nothing the box generates — engagements, packs the
agent installs inside — ever appears in the comparison or flows back.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path

#: host-workspace items synced into the box (files, dirs, or globs)
_SYNC_ITEMS = (
    "config.json",
    "packs",
    "skills",
    "prompts",
    "prompt.md",
    "SOUL.md",
    "drive_policy.json",
    "profiles",
)


def _ws_dir() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return Path(WORKSPACE_DIR)


def _hash_path(p: Path) -> str:
    if p.is_dir():
        parts = []
        for f in sorted(p.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                parts.append(f"{f.relative_to(p)}:{_hash_file(f)}")
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    return _hash_file(p)


def _hash_file(p: Path) -> str:
    with contextlib.suppress(Exception):
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return "?"


def host_manifest() -> dict:
    """{item: hash} for every sync item present on the host."""
    out: dict[str, str] = {}
    with contextlib.suppress(Exception):
        ws = _ws_dir()
        for item in _SYNC_ITEMS:
            p = ws / item
            if p.exists():
                out[item] = _hash_path(p)
    return out


def box_manifest(read_box) -> dict:
    """The manifest the box last synced against. read_box(path) -> str
    (the engine's file reader). Stored as {"synced": {item: hash}}."""
    with contextlib.suppress(Exception):
        d = json.loads(read_box(".sync-manifest.json") or "{}") or {}
        if isinstance(d.get("synced"), dict):
            return d["synced"]
        return d if d else {}
    return {}


def read_box_file(path: str) -> str:
    """Read one file from the box workspace (empty string on missing)."""
    from suijin.server.box import engine as bx

    rc, out = bx.run_in_box(f"cat {bx.BOX_WS}/{path} 2>/dev/null", timeout=60)
    return out if rc == 0 else ""


def write_box_file(path: str, content: str) -> bool:
    from suijin.server.box import engine as bx

    quoted = content.replace("'", "'\\''")
    rc, _ = bx.run_in_box(f"mkdir -p {bx.BOX_WS} && printf '%s' '{quoted}' > {bx.BOX_WS}/{path}", timeout=60)
    return rc == 0


def drift() -> dict:
    """{changed: [items], missing_tools: [names], manifest: {...}} —
    comparing live host against the box's last-sync snapshot."""
    from suijin.server.box import engine as bx

    host = host_manifest()
    have = box_manifest(read_box_file)
    changed = [i for i, h in host.items() if have.get(i) != h]
    # tools drift: host container.tools vs the box's DECLARED presence
    # checks (command -v alone falsely reports python-module deps like
    # requests/impacket as missing)
    missing: list[str] = []
    with contextlib.suppress(Exception):
        from suijin.server.box.provision import presence_script

        want = tool_list()
        if want:
            rc, out = bx.run_in_box(presence_script(want), timeout=120)
            if rc == 0:
                missing = [ln.split()[0] for ln in out.splitlines() if ln.endswith(" missing")]
    return {"changed": changed, "missing_tools": missing, "manifest": host}


#: tools that never belong in a headless box: wireless/hardware
#: attackers with no radio, heavyweight DB/security suites that pull
#: gigabytes. The box is for web-app work; add anything here back via
#: container.tools in config.json.
_BOX_EXCLUDE = {
    "aircrack-ng",
    "airodump-ng",
    "reaver",
    "wifite",
    "bully",
    "hostapd",
    "gvm",
    "gvm-tools",
    "python-gvm",
    "neo4j",
    "metasploit",
    "metasploit-framework",
    "msfconsole",
}


def tool_list() -> list[str]:
    """The tools a box installs — host config container.tools, default
    the doctor's curated set MINUS the headless-excluded (wireless,
    heavyweight suites): minimal disk, no radio-hardware tools that
    would hang or bloat a container."""
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        t = (load_config() or {}).get("container", {}).get("tools")
        if isinstance(t, list) and t:
            return sorted({str(x).strip() for x in t if str(x).strip()})
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.availability import _INSTALL

        return sorted(k for k in _INSTALL if k not in _BOX_EXCLUDE)
    return []


def sync_selected(items: list[str], log=print) -> list[str]:
    """Copy selected sync items host → box + refresh the snapshot.
    Returns the items actually synced."""
    from suijin.server.box import engine as bx

    done: list[str] = []
    host = host_manifest()
    ws = _ws_dir()
    for item in items:
        if item not in host:
            continue
        src = ws / item
        if src.is_dir():
            # tar-stream the dir across (engine-agnostic)
            import subprocess

            try:
                tar = subprocess.run(["tar", "czf", "-", "-C", str(ws), item], capture_output=True, timeout=300)
                if tar.returncode == 0:
                    quoted = bx.BOX_WS
                    # --warning=no-unknown-keyword: the host tar embeds
                    # mac xattrs (com.apple.provenance) as pax headers;
                    # gnu tar on the linux side warns about every one of
                    # them. They are meaningless there — silence it, and
                    # capture so nothing leaks to the operator's terminal.
                    extract_cmd = f"mkdir -p {quoted} && tar xzf - --warning=no-unknown-keyword -C {quoted}"
                    proc = subprocess.run(
                        ["orb", "-m", bx.BOX_NAME, "bash", "-lc", extract_cmd]
                        if shutil_which_orb()
                        else [
                            "docker",
                            "exec",
                            "-i",
                            bx.BOX_NAME,
                            "bash",
                            "-lc",
                            extract_cmd,
                        ],
                        input=tar.stdout,
                        capture_output=True,
                        timeout=300,
                    )
                    if proc.returncode == 0:
                        done.append(item)
            except Exception as e:  # noqa: BLE001
                log(f"sync {item}: {e}")
        else:
            with contextlib.suppress(Exception):
                content = src.read_text(encoding="utf-8", errors="replace")
                if write_box_file(item, content):
                    done.append(item)
    if done:
        manifest = json.dumps({"synced": {k: host[k] for k in done if k in host}})
        write_box_file(".sync-manifest.json.partial", manifest)
        # merge partial into existing snapshot
        bx.run_in_box(
            f"cd {bx.BOX_WS} && "
            'python3 -c "import json,os; '
            "m=json.load(open('.sync-manifest.json')) if os.path.exists('.sync-manifest.json') else {}; "
            "m.update(json.load(open('.sync-manifest.json.partial'))); "
            "json.dump(m, open('.sync-manifest.json','w'))\" 2>/dev/null || "
            f"cp {bx.BOX_WS}/.sync-manifest.json.partial {bx.BOX_WS}/.sync-manifest.json",
            timeout=60,
        )
    # secrets: the repo .env rides along when config synced
    with contextlib.suppress(Exception):
        env_src = Path(os.environ.get("SUIJIN_ENV") or "")
        if not env_src.exists():
            from suijin.server.box.engine import repo_root

            r = repo_root()
            env_src = Path(r) / ".env" if r else None
        if env_src and env_src.exists():
            write_box_file(".env", env_src.read_text(encoding="utf-8", errors="replace"))
    return done


def shutil_which_orb() -> bool:
    import shutil

    return shutil.which("orb") is not None
