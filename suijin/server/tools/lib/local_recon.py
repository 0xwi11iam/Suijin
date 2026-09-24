"""Local recon toolkit — the local-device equivalents of the remote arsenal.

An agent on a THIS-MACHINE engagement (privesc assessment, hardening
audit) used to face a wall of web-shaped tools: nmap against its own
laptop, http_request at localhost. These are the local natives — one
call each, platform-portable (macOS/Linux), bounded output, no tty
assumptions (non-interactive doctrine throughout):

  local_sys_info    OS/kernel/arch/host/user/groups/uptime
  local_priv_check  sudo -n posture, SUID/SGID binaries, world-writable
  local_services    service managers + listening sockets
  local_users       accounts, admin groups, last logins
  local_sched       cron / launchd agents / systemd timers
  local_cred_hunt   credential caches (ssh keys+agent, aws, kube, hist)
  local_proc        process table
  local_net         interfaces, routes, listeners

Everything here runs READ-ONLY enumeration. No mutations, ever.
"""

from __future__ import annotations

import os
import platform
import subprocess

_CAP = 12000


def _run(cmd: list[str], timeout: int = 15) -> str:
    """Run one enumeration command, best-effort, output-bounded."""
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
    except Exception as e:  # noqa: BLE001 — enumeration never raises
        return f"(error: {e})"


def _first(*outs: str) -> str:
    for o in outs:
        if o and not o.startswith("("):
            return o
    return ""


def _head(text: str, n: int = 120) -> str:
    return "\n".join(text.splitlines()[:n])[:_CAP]


def local_sys_info() -> str:
    """OS, kernel, arch, hostname, identity, uptime — the engagement's
    first call on a local assessment."""
    uname = platform.uname()
    lines = [
        f"host:     {uname.node}",
        f"os:       {uname.system} {uname.release} ({uname.version})",
        f"arch:     {uname.machine}",
        f"python:   {platform.python_version()}",
    ]
    if uname.system == "Darwin":
        lines.append(f"sw_vers:  {_run(['sw_vers']).replace(chr(10), ' · ')}")
    lines.append(f"uptime:   {_first(_run(['uptime']))}")
    lines.append(f"id:       {_first(_run(['id']))}")
    lines.append(f"whoami:   {_first(_run(['whoami']))}")
    lines.append(f"cwd:      {os.getcwd()}")
    return "\n".join(lines)


def local_priv_check() -> str:
    """Privilege escalation surface: sudo posture (non-interactive only),
    SUID/SGID binaries, world-writable sensitive paths."""
    out = ["== sudo (non-interactive) =="]
    sudo = _run(["sudo", "-n", "-l"])
    out.append(
        sudo
        if sudo and "password" not in sudo.lower()
        else "(no passwordless sudo — a password prompt is out of doctrine)"
    )
    out.append("\n== SUID/SGID binaries (common roots) ==")
    suid = _first(
        _run(
            [
                "find",
                "/bin",
                "/sbin",
                "/usr/bin",
                "/usr/sbin",
                "/usr/local/bin",
                "/opt/homebrew/bin",
                "-type",
                "f",
                "-perm",
                "-4000",
                "-o",
                "-type",
                "f",
                "-perm",
                "-2000",
            ],
            timeout=25,
        ),
        _run(["find", "/", "-maxdepth", "3", "-type", "f", "-perm", "-4000"], timeout=25),
    )
    out.append(_head(suid) or "(none found in scanned roots)")
    out.append("\n== world-writable sensitive dirs ==")
    ww = _run(["find", "/etc", "/usr/local", "/Library", "-maxdepth", "2", "-type", "d", "-perm", "-o+w"], timeout=20)
    out.append(_head(ww, 40) or "(none)")
    return "\n".join(out)[:_CAP]


def local_services() -> str:
    """Service managers + listening sockets (the local attack surface)."""
    out = []
    svc = _first(
        _run(["systemctl", "list-units", "--type=service", "--state=running", "--no-pager"]),
        _run(["launchctl", "list"], timeout=20),
    )
    out.append("== services ==")
    out.append(_head(svc, 60) or "(no service manager enumerated)")
    out.append("\n== listening sockets ==")
    listeners = _first(
        _run(["lsof", "-nP", "-i", "-P", "|", "grep", "-i", "listen"], timeout=20),
        _run(["lsof", "-nP", "-i", "-P"], timeout=20),
        _run(["netstat", "-tlnp"], timeout=20),
        _run(["netstat", "-an"], timeout=20),
    )
    rows = [ln for ln in listeners.splitlines() if "LISTEN" in ln.upper()] or listeners.splitlines()
    out.append(_head("\n".join(rows), 60) or "(none)")
    return "\n".join(out)[:_CAP]


def local_users() -> str:
    """Accounts, admin groups, recent logins."""
    out = ["== admin group =="]
    out.append(
        _first(
            _run(["dscl", ".", "-read", "/Groups/admin", "GroupMembership"]),
            _run(["getent", "group", "sudo", ";", "getent", "group", "wheel"]),
        )
    )
    out.append("\n== user accounts (real shells) ==")
    passwd = _first(_run(["dscl", ".", "-list", "/Users"]))
    if passwd:
        out.append("(macOS — dscl user list below; note local accounts usually uid >= 501)")
        out.append(_head(passwd, 50))
    else:
        pw = _run(["sh", "-c", "grep -v nologin /etc/passwd | grep -v /bin/false"])
        out.append(_head(pw, 50) or "(none)")
    out.append("\n== last logins ==")
    out.append(_head(_first(_run(["last", "-10"]), _run(["last", "-n", "10"])), 15) or "(n/a)")
    return "\n".join(out)[:_CAP]


def local_sched() -> str:
    """Persistence paths: cron, launchd agents, systemd timers."""
    out = ["== cron =="]
    crons = _run(["sh", "-c", "cat /etc/crontab 2>/dev/null; ls /etc/cron.d 2>/dev/null; crontab -l 2>/dev/null"])
    out.append(_head(crons, 40) or "(no user crontab)")
    out.append("\n== launchd agents (user) ==")
    la = _run(
        [
            "sh",
            "-c",
            "ls -la ~/Library/LaunchAgents 2>/dev/null; ls -la /Library/LaunchAgents 2>/dev/null | head -20; ls /Library/LaunchDaemons 2>/dev/null | head -20",
        ]
    )
    out.append(_head(la, 50) or "(none)")
    out.append("\n== systemd timers ==")
    out.append(_head(_run(["systemctl", "list-timers", "--no-pager"]), 20) or "(no systemd)")
    return "\n".join(out)[:_CAP]


def local_cred_hunt() -> str:
    """Credential caches readable non-interactively (READ-ONLY: lists and
    fingerprints, never key material) — ssh, aws, kube, histories."""
    home = os.path.expanduser("~")
    out = []
    ssh_dir = os.path.join(home, ".ssh")
    if os.path.isdir(ssh_dir):
        names = [f for f in sorted(os.listdir(ssh_dir)) if not f.startswith(".")]
        out.append(f"== ~/.ssh ({len(names)} entries) ==")
        for n in names[:20]:
            p = os.path.join(ssh_dir, n)
            try:
                import hashlib

                with open(p, "rb") as f:
                    blob = f.read()
                fp = hashlib.md5(blob).hexdigest()[:10]
                out.append(f"  {n}  ({os.path.getsize(p)}B, md5 {fp})")
            except OSError:
                out.append(f"  {n}  (unreadable)")
        agent = _run(["sh", "-c", "echo $SSH_AUTH_SOCK; ssh-add -l 2>&1"])
        out.append(f"agent: {agent.splitlines()[0] if agent else 'none'}")
    for label, sub in (("aws", ".aws"), ("kube", ".kube"), ("gcloud", ".config/gcloud"), ("gnupg", ".gnupg")):
        d = os.path.join(home, sub)
        if os.path.isdir(d):
            out.append(f"== ~/{sub} ({label}) present: {sorted(os.listdir(d))[:10]}")
    out.append("\n== shell histories ==")
    for h in (".zsh_history", ".bash_history"):
        p = os.path.join(home, h)
        if os.path.isfile(p):
            out.append(f"  {h}: {os.path.getsize(p)}B")
    return "\n".join(out)[:_CAP] or "(no credential caches found)"


def local_proc(pattern: str = "") -> str:
    """Process table (optionally filtered by pattern)."""
    out = _run(["ps", "aux"])
    if not out:
        out = _run(["ps", "-ef"])
    rows = out.splitlines()
    if pattern:
        rows = [rows[0]] + [r for r in rows[1:] if pattern.lower() in r.lower()]
    return _head("\n".join(rows), 80)


def local_net() -> str:
    """Interfaces, routes, listeners — the local network posture."""
    out = ["== interfaces =="]
    out.append(_head(_first(_run(["ifconfig"]), _run(["ip", "addr"])), 30))
    out.append("\n== routes ==")
    out.append(_head(_first(_run(["netstat", "-rn"]), _run(["ip", "route"])), 25))
    return "\n".join(out)[:_CAP]
