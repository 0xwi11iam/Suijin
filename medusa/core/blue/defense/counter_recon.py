"""Counter-recon — passive OSINT on attacker infrastructure."""
def recon_attacker(ip: str) -> dict:
    import socket
    result = {"ip": ip}
    try: result["hostname"] = socket.gethostbyaddr(ip)[0]
    except: result["hostname"] = "unknown"
    return result
