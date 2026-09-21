"""Local/post-exploitation operations — the exploitation half of the local
toolkit (local_recon.py is the recon half).

Two theaters:

  SAME COMPUTER (the attack host is the target):
    local_lpe_scan        consolidated privesc sweep (one call, all checks)
    local_suid_audit      SUID/SGID + known-abusable-bin matching
    local_path_hijack     writable dirs shadowing root-executed binaries
    local_service_hijack  services/agents pointing at user-writable paths
    local_docker_sock     the docker-socket instant-root path
    local_hist_search     secret/command mining in shell histories
    local_env_secrets     tokens in the environment + launchctl getenv
    local_file_find       privileged/writable file hunting
    local_mounts          mounts + noexec/nosuid flags

  SSH-REACHED TARGET (a box the agent has credentials for):
    ssh_exec              run a command remotely (BatchMode — no prompts)
    ssh_pull / ssh_push   file transfer over scp
    ssh_inventory         ~/.ssh/config + known_hosts → reachable targets

Everything is non-interactive (BatchMode, sudo -n), evidence-first
(output is returned, never just asserted), and READ-ONLY unless the
tool's whole point is a transfer (ssh_pull/ssh_push write where the
operator's command says). No mutations to system state, ever.
"""

from __future__ import annotations

import os
import re
import subprocess

_CAP = 12000


def _run(cmd, timeout: int = 20) -> str:
    if isinstance(cmd, str):
        cmd = ["sh", "-c", cmd]
    try:
        env = {
            **os.environ,
            "PATH": f"/opt/homebrew/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:{os.environ.get('PATH', '')}",
        }
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        out = (r.stdout or "") + (("\n[stderr] " + r.stderr) if r.stderr.strip() and r.returncode != 0 else "")
        return out.strip()
    except FileNotFoundError:
        return ""
    except subprocess.TimeoutExpired:
        return "(timed out)"
    except Exception as e:  # noqa: BLE001
        return f"(error: {e})"


def _head(text: str, n: int = 120) -> str:
    return "\n".join(text.splitlines()[:n])[:_CAP]


# ── same-computer: exploitation surface ──────────────────────────────

# the classic abusable locals (GTFOBins-style, name match only)
_ABUSABLE = re.compile(
    r"(^|/)(sudo|find|vim|vi|nmap|python[0-9.]*|perl|ruby|php|awk|gawk|tar|cp|mv|wget|curl|bash|sh|zsh|"
    r"env|less|more|man|tee|ed|nano|pico|cpulimit|docker|fleetctl|at|script|time|taskset|nc|ncat|netcat)$"
)

_LPE_CHECKS = (
    ("sudo -n -l (passwordless sudo)", "sudo -n -l 2>/dev/null | head -20"),
    (
        "SUID/SGID binaries",
        "find /bin /sbin /usr/bin /usr/sbin /usr/local/bin /opt/homebrew/bin -type f \\( -perm -4000 -o -perm -2000 \\) 2>/dev/null | head -40",
    ),
    (
        "writable dirs on root's PATH",
        'sh -c \'IFS=:; for d in $(sudo -n printenv PATH 2>/dev/null || echo /usr/bin); [ -w "$d" ] && echo "$d"; done\' 2>/dev/null',
    ),
    (
        "docker socket",
        "ls -l /var/run/docker.sock 2>/dev/null && (id -Gn | grep -qw docker && echo '-> group docker: CONTAINER=HOST ROOT')",
    ),
    (
        "world-writable /etc + /Library paths",
        "find /etc /Library /usr/local -maxdepth 2 -type d -perm -o+w 2>/dev/null | head -20",
    ),
    ("user launchd agents (persistence)", "ls ~/Library/LaunchAgents /Library/LaunchAgents 2>/dev/null | head -20"),
    ("file capabilities", "getcap -r /usr /opt 2>/dev/null | head -15"),
    (
        "writable service binaries",
        'sh -c \'for b in $(launchctl list 2>/dev/null | awk \\"{print \\\\\\$3}\\" | grep ^/ | head -20); [ -w \\"$b\\" ] && echo \\"writable: $b\\"; done\' 2>/dev/null',
    ),
    ("credential caches present", "ls -d ~/.ssh ~/.aws ~/.kube ~/.gnupg 2>/dev/null"),
    ("NFS/sshfs mounts", "mount | grep -Ei 'nfs|sshfs' | head -10"),
)


def local_lpe_scan() -> str:
    """One call, the whole local privesc sweep — every check, findings
    first (matches flagged), evidence included."""
    out = ["LOCAL PRIVILEGE-ESCALATION SWEEP", "=" * 40]
    hits = []
    sections = []
    for label, cmd in _LPE_CHECKS:
        res = _run(cmd, timeout=25)
        if not res or res.startswith("("):
            continue
        flag = ""
        low = res.lower()
        if "sudo" in label.lower() and "(all)" in low:
            flag = "  << FINDING: passwordless ALL sudo"
        if "suid" in label.lower() and _ABUSABLE.search(res.replace("\n", " ")):
            flag = "  << FINDING: abusable SUID/SGID present (see list)"
        if "docker" in label.lower() and "CONTAINER=HOST" in res:
            flag = "  << FINDING: docker group = host root"
        if "writable" in label.lower() and res.strip() and "writable" in low:
            flag = "  << REVIEW: writable paths in privileged execution paths"
        sections.append(f"-- {label}{flag}\n{res or '(none)'}")
        if flag:
            hits.append(f"{label}{flag}")
    if hits:
        out.append("FINDINGS:")
        out.extend(f"  * {h}" for h in hits)
        out.append("")
    out.extend(sections)
    return "\n".join(out)[:_CAP]


def local_suid_audit() -> str:
    """SUID/SGID enumeration WITH abusable-binary matching."""
    raw = _run(
        "find /bin /sbin /usr/bin /usr/sbin /usr/local/bin /opt/homebrew/bin /usr/libexec -type f \\( -perm -4000 -o -perm -2000 \\) 2>/dev/null"
    )
    if not raw:
        return "(no SUID/SGID binaries found in scanned roots)"
    lines = raw.splitlines()
    flagged = [ln for ln in lines if _ABUSABLE.search(ln)]
    out = []
    if flagged:
        out.append("ABUSABLE (GTFOBins-class — check escalation paths):")
        out.extend(f"  !! {f}" for f in flagged)
        out.append("")
    out.append(f"all SUID/SGID ({len(lines)}):")
    out.extend(f"  {ln}" for ln in lines[:60])
    return "\n".join(out)[:_CAP]


def local_path_hijack() -> str:
    """Writable directories that could shadow binaries root executes."""
    checks = [
        _run('IFS=:; for d in $PATH; do [ -w "$d" ] && echo "writable-on-MY-path: $d"; done'),
        _run("sudo -n printenv PATH 2>/dev/null"),
        _run("cat /etc/paths 2>/dev/null"),
    ]
    out = []
    if checks[0]:
        out.append("== writable dirs on MY PATH (hijack when a privileged context shares it) ==")
        out.append(checks[0])
    if checks[1]:
        out.append(f"\n== root's PATH (via sudo -n) ==\n{checks[1]}")
        out.append("(any overlap with writable dirs above = hijack)")
    if checks[2]:
        out.append(f"\n== /etc/paths ==\n{checks[2]}")
    return "\n".join(out)[:_CAP] or "(no writable PATH components — clean)"


def local_service_hijack() -> str:
    """Services/agents whose binaries or plists live in user-writable
    paths — the persistence + privesc twofer."""
    out = ["== user-writable LaunchAgents (mine) =="]
    out.append(_head(_run("ls -la ~/Library/LaunchAgents 2>/dev/null"), 20) or "(none)")
    out.append("\n== system LaunchAgents/Daemons pointing into /Users ==")
    out.append(
        _head(
            _run("grep -l '/Users/' /Library/LaunchAgents/*.plist /Library/LaunchDaemons/*.plist 2>/dev/null"),
            20,
        )
        or "(none — good)"
    )
    out.append("\n== running services with user-writable binaries ==")
    out.append(
        _head(
            _run(
                'sh -c \'launchctl list 2>/dev/null | awk "{print \\$3}" | grep ^/ | while read b; do [ -w "$b" ] && echo "writable: $b"; done\''
            ),
            20,
        )
        or "(none — good)"
    )
    return "\n".join(out)[:_CAP]


def local_docker_sock() -> str:
    """The docker-socket path: presence + group membership + what it
    would mean."""
    sock = "/var/run/docker.sock"
    out = []
    if not os.path.exists(sock):
        out.append("no /var/run/docker.sock on this host")
    else:
        out.append(_run(f"ls -l {sock}"))
        groups = _run("id -Gn")
        if "docker" in groups.split():
            out.append("<< FINDING: current user IS in docker group — docker run -v /:/mnt instantly roots the host")
        else:
            out.append("(user not in docker group — socket present but not directly usable)")
    sock2 = os.path.expanduser("~/.docker/run/docker.sock")
    if os.path.exists(sock2):
        out.append(f"rootless socket also present: {sock2} (rootless daemon = lower risk)")
    out.append(_run("docker version --format '{{.Server.Version}}' 2>/dev/null") and "\ndocker CLI usable: yes" or "")
    return "\n".join(x for x in out if x)[:_CAP]


def local_hist_search(pattern: str = "sudo|password|token|api_key|secret|ssh ") -> str:
    """Mine shell histories (mine or a reached user's HOME) for secrets
    and privileged commands. READ-ONLY."""
    homes = [os.path.expanduser("~")]
    pat = str(pattern or "sudo|password|token|api_key|secret|ssh ").strip()
    out = []
    for home in homes:
        for h in (".zsh_history", ".bash_history", ".sh_history"):
            p = os.path.join(home, h)
            if os.path.isfile(p):
                res = _run(f"grep -aiE '{pat}' {p!r} 2>/dev/null | tail -40")
                n = len(res.splitlines()) if res else 0
                out.append(f"-- {p} ({n} match(es) for /{pat}/)")
                out.append(_head(res, 40))
    return "\n".join(out)[:_CAP] or "(no history matches)"


def local_env_secrets() -> str:
    """Tokens in the live environment + launchctl's global env (macOS)."""
    out = ["== environment (secret-shaped names) =="]
    env_rows = [
        f"  {k}={str(v)[:40]}..." if len(str(v)) > 40 else f"  {k}={v}"
        for k, v in sorted(os.environ.items())
        if re.search(r"(token|key|secret|pass|auth|cred)", k, re.I)
    ]
    out.extend(env_rows or ["  (none)"])
    lc = _run(
        "launchctl getenv 2>/dev/null; launchctl print user/$(id -u) 2>/dev/null | grep -iA1 'environment' | head -10"
    )
    if lc:
        out.append("\n== launchctl environment ==")
        out.append(_head(lc, 15))
    return "\n".join(out)[:_CAP]


def local_file_find(name: str = "", newer_than_days: int = 0, root: str = "/") -> str:
    """Find files by name pattern and/or recency under a root — the local
    counterpart of remote file hunting. Bounded, permission-tolerant."""
    name = str(name or "").strip()
    days = int(newer_than_days or 0)
    if not name and not days:
        return "Error: give name and/or newer_than_days (e.g. name='*.plist', newer_than_days=3)."
    root = str(root or "/")
    cmd = f"find {root!r} -maxdepth 6 -type f"
    if name:
        cmd += f" -name {name!r}"
    if days:
        cmd += f" -mtime -{days}"
    cmd += " 2>/dev/null | head -80"
    res = _run(cmd, timeout=30)
    return _head(res, 80) or "(no matches)"


def local_mounts() -> str:
    """Mount table with security flags — noexec/nosuid matter for
    exploitation planning."""
    res = _run("mount")
    out = _head(res, 50)
    flagged = [ln for ln in res.splitlines() if ("nfs" in ln.lower() or "sshfs" in ln.lower())]
    if flagged:
        out += "\n\n== network mounts (no_root_squash NFS = classic privesc) =="
        out += "\n".join(flagged[:10])
    return out[:_CAP]


# ── ssh-reached target ───────────────────────────────────────────────


def _ssh_base(host: str, user: str = "", port: int = 22, key: str = "") -> list[str]:
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=8",
        "-p",
        str(port or 22),
    ]
    if user:
        cmd += ["-l", str(user)]
    if key:
        cmd += ["-i", os.path.expanduser(str(key))]
    cmd.append(str(host))
    return cmd


def ssh_exec(host: str, cmd: str, user: str = "", port: int = 22, key: str = "", timeout: int = 60) -> str:
    """Run a command on a remote box over SSH (BatchMode — keys/agents
    only, password prompts are out of doctrine). This is how the agent
    works a target AFTER credential access."""
    host = str(host or "").strip()
    command = str(cmd or "").strip()
    if not host or not command:
        return "Error: host and cmd required."
    full = _ssh_base(host, user, port, key) + [command]
    r = subprocess.run(full, capture_output=True, text=True, timeout=max(5, min(int(timeout or 60), 300)))
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    head = f"[ssh {user + '@' if user else ''}{host}:{port}] exit {r.returncode}\n"
    body = out[:_CAP]
    if not body and err:
        body = f"(stderr)\n{err[:2000]}"
    return head + body


def ssh_pull(host: str, remote_path: str, local_path: str = "", user: str = "", port: int = 22, key: str = "") -> str:
    """Fetch a file from the target (scp). local_path defaults to the
    engagement home."""
    if not host or not str(remote_path or "").strip():
        return "Error: host and remote_path required."
    dest = os.path.expanduser(str(local_path or "")) or None
    if dest is None:
        from suijin.modules.platform.lib import workspace

        dest = str(workspace.home_dir() / os.path.basename(remote_path))
    spec = f"{user + '@' if user else ''}{host}:{remote_path}"
    r = subprocess.run(
        ["scp", "-P", str(port or 22), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]
        + (["-i", os.path.expanduser(str(key))] if key else [])
        + [spec, dest],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return f"[scp {spec} -> {dest}] exit {r.returncode}\n{(r.stderr or '').strip()[:500] or 'done'}"


def ssh_push(host: str, local_path: str, remote_path: str, user: str = "", port: int = 22, key: str = "") -> str:
    """Send a file to the target (scp)."""
    src = os.path.expanduser(str(local_path or "").strip())
    if not host or not src or not os.path.isfile(src):
        return f"Error: host + existing local_path required ({src!r})."
    if not str(remote_path or "").strip():
        return "Error: remote_path required."
    spec = f"{user + '@' if user else ''}{host}:{remote_path}"
    r = subprocess.run(
        ["scp", "-P", str(port or 22), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]
        + (["-i", os.path.expanduser(str(key))] if key else [])
        + [src, spec],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return f"[scp {src} -> {spec}] exit {r.returncode}\n{(r.stderr or '').strip()[:500] or 'done'}"


def ssh_inventory() -> str:
    """Parse ~/.ssh/config + known_hosts into a reachable-target
    inventory (hosts, users, keys, ports) — the post-credential map."""
    home = os.path.expanduser("~")
    out = []
    cfg_path = os.path.join(home, ".ssh", "config")
    if os.path.isfile(cfg_path):
        out.append("== ~/.ssh/config ==")
        try:
            with open(cfg_path, encoding="utf-8", errors="ignore") as f:
                out.append(_head(f.read(), 60))
        except OSError:
            out.append("(unreadable)")
    else:
        out.append("(no ~/.ssh/config)")
    kh = os.path.join(home, ".ssh", "known_hosts")
    if os.path.isfile(kh):
        res = _run(f"awk '{{print $1}}' {kh!r} 2>/dev/null | sort -u | head -40")
        out.append(f"\n== known_hosts ({len(res.splitlines()) if res else 0} unique) ==")
        out.append(_head(res, 40))
    keys = (
        [
            f
            for f in sorted(os.listdir(os.path.join(home, ".ssh")))
            if f.startswith(("id_", "key_")) and not f.endswith(".pub")
        ]
        if os.path.isdir(os.path.join(home, ".ssh"))
        else []
    )
    if keys:
        out.append(f"\n== private keys present: {', '.join(keys[:10])}")
    return "\n".join(out)[:_CAP]
