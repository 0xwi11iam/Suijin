"""Container escape analysis — real config inspection, concrete escape commands.

Never simulates: every finding is derived from actual Docker API
responses or real filesystem reads (self-analysis mode)."""

import json
from pathlib import Path

import requests

_TIMEOUT = (5, 15)


def _api(base, path):
    """Call the Docker API, return (data, error)."""
    try:
        r = requests.get(f"{base.rstrip('/')}{path}", timeout=_TIMEOUT)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code} from {path}"
        return r.json(), ""
    except requests.RequestException as e:
        return None, f"{type(e).__name__}: {e}"


def docker_analyze(url: str = "http://127.0.0.1:2375") -> str:
    """Analyze a Docker API endpoint for escape vectors."""
    base = url.strip() or "http://127.0.0.1:2375"

    # 1: API reachable?
    ver, err = _api(base, "/version")
    if err:
        return f"Error: Docker API unreachable at {base} ({err})"
    lines = [f"Docker API: {ver.get('Version', '?')} (API {ver.get('ApiVersion', '?')})"]

    # 2: list containers
    containers, err = _api(base, "/containers/json?all=1")
    if err:
        lines.append(f"containers list failed: {err}")
        return "\n".join(lines)
    lines.append(f"\n{len(containers)} container(s) found")

    findings = []

    for c in containers[:10]:
        cid = c.get("Id", "?")[:12]
        names = c.get("Names") or ["?"]
        cname = names[0].lstrip("/")

        # 3: inspect for escape vectors
        detail, err = _api(base, f"/containers/{c['Id']}/json")
        if err:
            continue

        host_config = detail.get("HostConfig", {})
        privileged = host_config.get("Privileged", False)
        caps = host_config.get("CapAdd") or []
        mounts = host_config.get("Binds") or []
        devices = host_config.get("Devices") or []

        if privileged:
            findings.append(
                f"PRIVILEGED container '{cname}' ({cid}) — full host access\n"
                f"  escape: nsenter --target 1 --mount --uts --ipc --net --pid -- bash"
            )

        dangerous_caps = {"SYS_ADMIN", "SYS_PTRACE", "NET_ADMIN", "DAC_READ_SEARCH", "MKNOD", "SYS_CHROOT"}
        if set(caps) & dangerous_caps:
            found = sorted(set(caps) & dangerous_caps)
            findings.append(
                f"capabilities on '{cname}' ({cid}): {', '.join(found)}\n"
                f"  SYS_ADMIN -> mount -t proc proc /proc && cat /proc/1/root/etc/shadow\n"
                f"  SYS_PTRACE -> inject into host PIDs via /proc/<host_pid>/mem"
            )

        host_mounts = [
            m for m in mounts if "/" in m and not m.startswith("/tmp") and not m.startswith("/var/lib/docker")
        ]
        socket_mount = any("docker.sock" in m for m in mounts)
        if socket_mount:
            findings.append(
                f"docker.sock mounted in '{cname}' ({cid}) — full Docker control\n"
                f"  escape: curl -s --unix-socket /var/run/docker.sock http://localhost/containers/json\n"
                f"  then: create privileged container with host / mounted"
            )
        elif host_mounts:
            findings.append(
                f"host paths mounted in '{cname}' ({cid}): {', '.join(host_mounts[:3])}\n"
                f"  check for: /etc (passwd/shadow), / (full root), ~/.ssh (keys)"
            )

        if devices:
            findings.append(
                f"devices in '{cname}' ({cid}): {len(devices)} device(s) — check for /dev/sda (disk access)"
            )

    if findings:
        lines.append(f"\n== ESCAPE VECTORS ({len(findings)}) ==")
        lines.extend("\n" + f for f in findings)
    else:
        lines.append("\nNo escape vectors found in the first 10 containers")

    return "\n".join(lines)


def escape_check() -> str:
    """Self-analysis: does the CURRENT container have escape vectors?"""
    findings = []

    # 1: are we in a container?
    cgroup = Path("/proc/1/cgroup")
    if not cgroup.exists():
        return "Error: /proc/1/cgroup not readable"
    cgroup_text = cgroup.read_text(errors="ignore")
    if "docker" not in cgroup_text and "containerd" not in cgroup_text and "kubepods" not in cgroup_text:
        return "Not running in a container (no docker/containerd/kubepods in cgroup)"

    # 2: docker.sock
    if Path("/var/run/docker.sock").exists():
        findings.append(
            "docker.sock present: full Docker API access\n"
            "  escape: curl -s --unix-socket /var/run/docker.sock http://localhost/containers/json"
        )

    # 3: capabilities
    caps_file = Path("/proc/self/status")
    if caps_file.exists():
        for line in caps_file.read_text(errors="ignore").splitlines():
            if line.startswith("CapEff:"):
                cap_hex = line.split()[1]
                # check for CAP_SYS_ADMIN (bit 21)
                try:
                    cap_val = int(cap_hex, 16)
                    if cap_val & (1 << 21):
                        findings.append(
                            "CAP_SYS_ADMIN effective: can mount, load modules, access host namespaces\n"
                            "  escape: nsenter --target 1 --mount --uts --ipc --net --pid -- bash"
                        )
                    if cap_val & (1 << 19):  # SYS_PTRACE = bit 19
                        findings.append("CAP_SYS_PTRACE effective: can inject into host processes via /proc/<pid>/mem")
                except ValueError:
                    pass

    # 4: host root mounted?
    root_mount = Path("/proc/mounts")
    if root_mount.exists():
        for line in root_mount.read_text(errors="ignore").splitlines():
            parts = line.split()
            if len(parts) >= 3:
                mount_point, fs_type = parts[1], parts[2]
                if mount_point == "/" and fs_type not in ("overlay", "aufs"):
                    findings.append(f"root filesystem is {fs_type} (not overlay) — may be host root")

    # 5: privileged detection via /dev
    if Path("/dev/sda").exists():
        findings.append(
            "/dev/sda visible — raw disk access possible\n  escape: fdisk -l /dev/sda; mount /dev/sda1 /mnt"
        )

    if findings:
        return "== SELF-ANALYSIS: ESCAPE VECTORS ==\n" + "\n".join(findings)
    return "In a container, but no escape vectors detected (restricted mode)"
