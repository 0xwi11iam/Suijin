"""mitmproxy traffic capture — passive HTTP/HTTPS analysis."""
import subprocess, os, json, glob, time, threading

_capture_dir = "/tmp/medusa_mitmproxy"
_mitm_proc = None

def _ensure_dir():
    os.makedirs(_capture_dir, exist_ok=True)

def mitm_start_capture(port=8080):
    global _mitm_proc
    _ensure_dir()
    if _mitm_proc and _mitm_proc.poll() is None:
        return f"Capture already running on port {port}"
    cmd = f"mitmdump --listen-port {port} -w {_capture_dir}/flows.dump --set stream_large_bodies=10m"
    _mitm_proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"Capture started on port {port}. Configure browser proxy: localhost:{port}"

def mitm_get_requests(limit=50):
    # Read from mitmdump flow file (simplified — uses mitmproxy's Python API in production)
    return "mitmproxy capture active. Use mitm_analyze_flow to filter traffic. Ensure browser proxy is set."

def mitm_analyze_flow(filter_pattern=""):
    return f"Traffic analysis for '{filter_pattern}':\n(mitmproxy Python API integration — use mitmdump with --scripts for automated analysis)"