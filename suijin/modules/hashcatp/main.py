import subprocess


def hashcat_crack(hash_str: str = "", mode: str = "", wordlist: str = "") -> str:
    if not hash_str or not mode:
        return "Error: hash (string or file) and mode required (identify_hash suggests modes)"
    argv = ["hashcat", "-m", str(mode).strip(), "--status", "--potfile-disable"]
    import os

    if os.path.isfile(hash_str):
        argv.append(hash_str)
    else:
        argv.append(hash_str.strip())
    if wordlist:
        argv.append(wordlist)
    else:
        return "Error: wordlist path required (rockyou or derived — mutate_wordlist can build one)"
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        return "Error: hashcat not installed"
    except subprocess.TimeoutExpired:
        return "Error: hashcat timed out after 30min"
    return f"exit={r.returncode}\n" + ((r.stdout or "") + (r.stderr or ""))[:10000]
