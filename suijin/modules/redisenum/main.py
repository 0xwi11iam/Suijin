import subprocess


def redis_info(host: str = "", port: int = 6379) -> str:
    if not host:
        return "Error: host required"
    base = ["redis-cli", "-h", host.strip(), "-p", str(port or 6379), "--no-auth-warning"]
    parts = []
    for args in (["INFO", "server"], ["INFO", "keyspace"], ["CONFIG", "GET", "dir"]):
        try:
            r = subprocess.run(base + args, capture_output=True, text=True, timeout=15)
            parts.append(f"$ {' '.join(args)}\n{(r.stdout or r.stderr).strip()[:3000]}")
        except FileNotFoundError:
            return "Error: redis-cli not installed"
        except subprocess.TimeoutExpired:
            parts.append(f"$ {' '.join(args)} — timeout (likely auth required or filtered)")
    return "\n\n".join(parts) + "\nRead-only checks only — never write via recon tools."
