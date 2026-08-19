import subprocess


def az_account_list() -> str:
    argv = ["az", "account", "list", "--output", "table"]
    missing = [a for a in argv if a == "@@"]
    if missing:
        return "Error: " + ", ".join(args_sig) + " required"
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return "Error: az not installed (see install hints)"
    except subprocess.TimeoutExpired:
        return "Error: timed out after 300s"
    return f"exit={r.returncode}\n" + ((r.stdout or "") + (r.stderr or ""))[:10000]
