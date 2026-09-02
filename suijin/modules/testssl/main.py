import subprocess


def testssl_scan(host: str = "") -> str:
    import shlex

    argv = ["testssl.sh", "--quiet", "--color", "0", host.strip()]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        return "Error: testssl.sh not installed (see install hints in the tool catalog)"
    except subprocess.TimeoutExpired:
        return "Error: testssl.sh timed out after 180s"
    out = (r.stdout or "") + (r.stderr and f"\n[stderr]\n{r.stderr}" or "")
    return f"exit={r.returncode}\n{out[:8000] or '(no output)'}\nLong-running; use for final report evidence."
