"""Counter-recon — passive OSINT on attacker infrastructure."""
def recon_attacker(ip: str) -> dict:
    import socket
    result = {"ip": ip}
    try: result["hostname"] = socket.gethostbyaddr(ip)[0]
    except Exception:
        import logging; logging.getLogger("medusa").warning(f"Counter-recon failed for {ip}", exc_info=True)
        result["hostname"] = "unknown"
    return result
