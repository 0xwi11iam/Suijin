import subprocess


def wafw00f_scan(url: str = "") -> str:
    import shlex

    argv = ["wafw00f", "-a", url.strip()]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        return "Error: wafw00f not installed (see install hints in the tool catalog)"
    except subprocess.TimeoutExpired:
        return "Error: wafw00f timed out after 180s"
    out = (r.stdout or "") + (r.stderr and f"\n[stderr]\n{r.stderr}" or "")
    return f"exit={r.returncode}\n{out[:8000] or '(no output)'}\nAlways map the WAF before attack tuning."
