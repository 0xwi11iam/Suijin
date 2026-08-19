import socket
import ssl
from datetime import datetime, timezone


def ssl_cert_info(host: str = "", port: int = 443) -> str:
    if not host:
        return "Error: host required"
    try:
        port = int(port or 443)
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=8):
            with ctx.wrap_socket(socket.socket(), server_hostname=host) as s:
                s.settimeout(8)
                s.connect((host, port))
                cert = s.getpeercert()
        not_before = datetime.strptime(cert["notBefore"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days_left = (not_after - datetime.now(timezone.utc)).days
        sans = [v for k, v in cert.get("subjectAltName", [])]
        out = [
            f"issuer: {dict(x[0] for x in cert['issuer'])}",
            f"subject: {dict(x[0] for x in cert['subject'])}",
            f"valid: {cert['notBefore']} -> {cert['notAfter']} ({days_left}d left)",
            f"SANs ({len(sans)}): " + ", ".join(sans[:20]),
        ]
        if days_left < 14:
            out.append("WARN: expires within 14 days")
        wildcards = [s for s in sans if s.startswith("*")]
        if wildcards:
            out.append(f"wildcard SANs: {wildcards} (check scope width)")
        return "\n".join(out)
    except ssl.SSLCertVerificationError as e:
        return f"VERIFICATION FAILED (self-signed/mismatched/CA issue): {e.verify_message if hasattr(e, 'verify_message') else e}"
    except Exception as e:
        return f"Error: {e}"
