"""Log reader — tail, search, filter access logs."""
import subprocess
from pathlib import Path

def tail_logs(log_path: str, lines: int = 50) -> str:
    try:
        r = subprocess.run(["tail","-n",str(lines),log_path], capture_output=True, text=True, timeout=5)
        return r.stdout[:5000]
    except: return "Log read failed"

def search_logs(log_path: str, pattern: str) -> str:
    try:
        r = subprocess.run(["grep","-i",pattern,log_path], capture_output=True, text=True, timeout=5)
        return r.stdout[:5000] or "No matches"
    except: return "Search failed"

def filter_by_ip(log_path: str, ip: str) -> str:
    return search_logs(log_path, ip)
