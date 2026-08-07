"""Blue team shared utilities."""
import hashlib, re, ipaddress

def hash_request(req: dict) -> str: return hashlib.sha256(str(req).encode()).hexdigest()[:16]
def is_valid_ip(ip: str) -> bool:
    try: ipaddress.ip_address(ip); return True
    except: return False
def extract_iocs(text: str) -> dict:
    ips = re.findall(r'\d+\.\d+\.\d+\.\d+', text)
    urls = re.findall(r'https?://[^\s<>"]+', text)
    return {"ips": list(set(ips)), "urls": list(set(urls))}
