import subprocess


def aws_enum(profile: str = '', region: str = '') -> str:
    argv = ["aws", "--profile", profile or "default", "--region", region or "us-east-1", "sts", "get-caller-identity"]
    missing = [a for a in argv if a == "@@"]
    if missing:
        return "Error: " + ", ".join(args_sig) + " required"
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return "Error: aws not installed (see install hints)"
    except subprocess.TimeoutExpired:
        return "Error: timed out after 300s"
    return f"exit={r.returncode}\n" + ((r.stdout or "") + (r.stderr or ""))[:10000]  # authenticated enum ONLY, with written authorization
