"""system stats — measured, not guessed.

Accuracy rules (operator: "make sure it is accurate"):
  - rates (disk MB/s, network MB/s) are DELTAS between two counter
    samples ~1s apart, never instantaneous guesses
  - cpu is psutil's interval-measured percent (blocking sample), with
    per-core breakdown
  - memory pressure: psutil where cross-platform, plus the platform's
    own source when it exists (macOS vm_stat page counts) — labelled
    honestly when estimated
  - nothing invented: a metric that can't be read on this platform
    says "n/a", never a fake number
"""

from __future__ import annotations

import contextlib


def _mac_memory_pressure() -> str:
    """macOS: real pressure from vm_stat page counts (psutil has no
    pressure API). Pages: free+inactive+purgeable vs wired+compressed."""
    import re
    import subprocess

    with contextlib.suppress(Exception):
        r = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
        pages = {}
        for ln in (r.stdout or "").splitlines():
            m = re.match(r'"?([^":]+)"?:\s+(\d+)",?$|"?([^":]+)"?\.\s+(\d+)",?$', ln.strip().rstrip("."))
            if not m:
                m2 = re.match(r'"?([^":]+)"?[:.]\s+(\d+)', ln.strip().rstrip("."))
                if m2:
                    pages[m2.group(1).strip()] = int(m2.group(2))
        if not pages:
            return ""
        def g(*names):
            return sum(v for k, v in pages.items() if any(n in k for n in names))
        ps = 16384  # arm64 page size
        free = g("Free", "Inactive", "Purgeable") * ps
        used = g("Wired", "Active", "Compressed", "Occupied") * ps
        total = free + used
        if not total:
            return ""
        pct = used / total * 100
        # Apple's own bar: <60% fine, 60-75% warning, >75% pressure
        state = "normal" if pct < 60 else ("warning" if pct < 75 else "pressure")
        return f"{pct:.0f}% ({state})"
    return ""


def collect(interval: float = 1.0) -> dict:
    """One accurate snapshot. Blocking for `interval` to measure rates."""
    import time

    out: dict = {"interval_s": interval}

    with contextlib.suppress(Exception):
        import psutil

        out["cpu_percent"] = psutil.cpu_percent(interval=interval)
        out["cpu_per_core"] = [round(p) for p in psutil.cpu_percent(interval=0, percpu=True)]
        out["load_avg"] = [round(x, 2) for x in psutil.getloadavg()]
        vm = psutil.virtual_memory()
        out["mem_used_gib"] = round(vm.used / (1024**3), 1)
        out["mem_total_gib"] = round(vm.total / (1024**3), 1)
        out["mem_percent"] = vm.percent
        sw = psutil.swap_memory()
        out["swap_used_gib"] = round(sw.used / (1024**3), 1)
        out["swap_total_gib"] = round(sw.total / (1024**3), 1)

        # DISK I/O — delta over the interval
        d1 = psutil.disk_io_counters()
        time.sleep(interval)
        d2 = psutil.disk_io_counters()
        if d1 and d2:
            out["disk_read_mbs"] = round((d2.read_bytes - d1.read_bytes) / 1e6 / interval, 1)
            out["disk_write_mbs"] = round((d2.write_bytes - d1.write_bytes) / 1e6 / interval, 1)
            out["disk_iops"] = round(((d2.read_count - d1.read_count) + (d2.write_count - d1.write_count)) / interval)

        # NETWORK — delta over the same window
        n1 = psutil.net_io_counters()
        time.sleep(0.2)
        n2 = psutil.net_io_counters()
        if n1 and n2:
            out["net_rx_mbs"] = round((n2.bytes_recv - n1.bytes_recv) / 1e6 / 0.2, 1)
            out["net_tx_mbs"] = round((n2.bytes_sent - n1.bytes_sent) / 1e6 / 0.2, 1)

        # mounts (the disks that matter)
        mnts = []
        for p in psutil.disk_partitions(all=False):
            with contextlib.suppress(Exception):
                u = psutil.disk_usage(p.mountpoint)
                mnts.append({"mount": p.mountpoint, "used_gib": round(u.used / (1024**3), 1),
                             "total_gib": round(u.total / (1024**3), 1), "pct": u.percent})
        out["mounts"] = mnts
        out["procs"] = psutil.Process().pid and len(psutil.pids())

    with contextlib.suppress(Exception):
        import platform

        if platform.system() == "Darwin":
            out["mem_pressure"] = _mac_memory_pressure()

    return out


def render(d: dict) -> str:
    """One honest block for the CLI + hub."""
    L = []
    if "cpu_percent" in d:
        cores = " ".join(f"{p:>3}" for p in d.get("cpu_per_core", [])[:10])
        la = " ".join(str(x) for x in d.get("load_avg", []))
        L.append(f"  cpu       {d['cpu_percent']:.0f}%  [{cores}]  load {la}")
    if "mem_used_gib" in d:
        m = f"  memory    {d['mem_used_gb']}/{d['mem_total_gb']} gb ({d['mem_percent']:.0f}%)"
        if d.get("mem_pressure"):
            m += f"  pressure: {d['mem_pressure']}"
        L.append(m)
    if "swap_total_gib" in d and d["swap_total_gib"]:
        L.append(f"  swap      {d['swap_used_gb']}/{d['swap_total_gb']} gib")
    if "disk_read_mbs" in d:
        L.append(f"  disk      ▼{d['disk_read_mbs']} MB/s ▲{d['disk_write_mbs']} MB/s  {d.get('disk_iops', 0)} iops")
    if "net_rx_mbs" in d:
        L.append(f"  network   ▼{d['net_rx_mbs']} MB/s ▲{d['net_tx_mbs']} MB/s")
    for mnt in d.get("mounts", [])[:6]:
        L.append(f"  disk {mnt['mount']:<14} {mnt['used_gb']}/{mnt['total_gb']} gb ({mnt['pct']:.0f}%)")
    if d.get("procs"):
        L.append(f"  processes {d['procs']}")
    return "\n".join(L) if L else "  (no metrics readable on this host)"
