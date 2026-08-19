"""Log Surgeon — advanced log parsing."""

import subprocess, re


def log_search(filepath, pattern, context=3):
    try:
        r = subprocess.run(
            ["grep", "-i", "-C", str(context), pattern, filepath], capture_output=True, text=True, timeout=10
        )
        return r.stdout[:5000] or "No matches"
    except:
        return "Search failed"


def log_tail(filepath, lines=100):
    try:
        r = subprocess.run(["tail", "-n", str(lines), filepath], capture_output=True, text=True, timeout=5)
        return r.stdout[:5000]
    except:
        return "Tail failed"


def log_stats(filepath):
    try:
        with open(filepath) as f:
            content = f.read()
        ips = set(re.findall(r"\d+\.\d+\.\d+\.\d+", content))
        return f"Lines: {len(content.split(chr(10)))}, Unique IPs: {len(ips)}, Size: {len(content)} bytes"
    except:
        return "Stats failed"
