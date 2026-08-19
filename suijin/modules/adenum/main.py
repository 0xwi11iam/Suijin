import subprocess


def ad_null_session(dc: str = "") -> str:
    if not dc:
        return "Error: dc required"
    argv = [
        "python3",
        "-c",
        "import socket;s=socket.create_connection(('%s',388 if False else 389),5);b=s.recv(2048);print(len(b),b[:120])"
        % dc.strip(),
    ]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            return f"LDAP port OPEN, banner bytes: {r.stdout.strip()}\n(pair with impacket: GetNPUsers -no-pass, GetUserSPNs once you have creds)"
        return "LDAP 389 closed/filtered"
    except Exception as e:
        return f"Error: {e}"
