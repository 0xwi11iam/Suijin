import subprocess


def dirsearch_scan(url: str = "", extensions: str = "php,html,txt", wordlist: str = "") -> str:
    if not url:
        return "Error: url required"
    argv = ["dirsearch", "-u", url.strip(), "-e", (extensions or "php,html,txt").strip(), "--quiet", "-t", "15"]
    if wordlist:
        argv += ["-w", wordlist]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    except FileNotFoundError:
        return "Error: dirsearch not installed"
    except subprocess.TimeoutExpired:
        return "Error: dirsearch timed out after 600s"
    return (
        f"exit={r.returncode}\n"
        + ((r.stdout or "")[:12000] or "(no output)")
        + "\nGobuster (execute_terminal) is the faster alternative for big lists."
    )
