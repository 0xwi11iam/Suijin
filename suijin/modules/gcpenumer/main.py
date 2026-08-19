import subprocess


def gcp_whoami() -> str:
    argv = ["gcloud", "config", "get-value", "account"]
    missing = [a for a in argv if a == "@@"]
    if missing:
        return "Error: " + ", ".join(args_sig) + " required"
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return "Error: gcloud not installed (see install hints)"
    except subprocess.TimeoutExpired:
        return "Error: timed out after 300s"
    return f"exit={r.returncode}\n" + ((r.stdout or "") + (r.stderr or ""))[:10000]
